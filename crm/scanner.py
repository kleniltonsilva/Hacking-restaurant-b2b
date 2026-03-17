"""
crm/scanner.py - Orquestrador de scans do CRM
Executa pipeline de scraping direto do CRM usando PostgreSQL.
"""
import asyncio
import sys
import os
from datetime import datetime

# Garantir que o diretório raiz esteja no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_pg import (
    init_pg, criar_scan_job, atualizar_scan_job, scan_log,
    obter_cnpjs_sem_ifood, obter_cnpjs_sem_delivery,
    atualizar_ifood_receita, atualizar_delivery_receita,
    buscar_cnpjs_para_maps_direcionado, atualizar_lead_maps,
    marcar_maps_checked,
)
from config import DELIVERY_PLATAFORMAS, normalizar_cidade


# Scan ativo (para controlar cancelamento)
_scan_tasks = {}


async def executar_scan(job_id: int, cidades: list, etapas: list,
                         headless: bool = True):
    """Executa pipeline de scan completo.

    Args:
        job_id: ID do scan_job no banco
        cidades: Lista de [cidade, uf]
        etapas: Lista de etapas: 'maps', 'ifood', 'rappi', '99food'
        headless: Modo headless do browser
    """
    init_pg()

    atualizar_scan_job(job_id,
                       status="executando",
                       started_at=datetime.now())
    scan_log(job_id, f"Scan iniciado: {len(cidades)} cidade(s), etapas: {', '.join(etapas)}")

    total_processados = 0
    total_encontrados = 0
    total_erros = 0
    progresso = {}

    try:
        for i, (cidade, uf) in enumerate(cidades):
            cidade_norm = normalizar_cidade(cidade)
            cidade_key = f"{cidade_norm}/{uf}"
            progresso[cidade_key] = {"status": "executando", "etapas": {}}

            atualizar_scan_job(job_id,
                               cidade_atual=f"{cidade_norm}/{uf}",
                               progresso=progresso)
            scan_log(job_id, f"{'='*50}")
            scan_log(job_id, f"Cidade {i+1}/{len(cidades)}: {cidade_norm}/{uf}")
            scan_log(job_id, f"{'='*50}")

            # Maps Direcionada
            if "maps" in etapas:
                try:
                    stats = await _etapa_maps_direcionado(
                        job_id, cidade_norm, uf, headless
                    )
                    progresso[cidade_key]["etapas"]["maps"] = stats
                    total_processados += stats.get("processados", 0)
                    total_encontrados += stats.get("encontrados", 0)
                except Exception as e:
                    total_erros += 1
                    scan_log(job_id, f"[ERRO] Maps: {e}", "error")
                    progresso[cidade_key]["etapas"]["maps"] = {"erro": str(e)}

            # Delivery platforms
            for plat in ["ifood", "rappi", "99food"]:
                if plat not in etapas:
                    continue
                try:
                    stats = await _etapa_delivery(
                        job_id, cidade_norm, uf, plat, headless
                    )
                    progresso[cidade_key]["etapas"][plat] = stats
                    total_processados += stats.get("processados", 0)
                    total_encontrados += stats.get("encontrados", 0)
                except Exception as e:
                    total_erros += 1
                    scan_log(job_id, f"[ERRO] {plat}: {e}", "error")
                    progresso[cidade_key]["etapas"][plat] = {"erro": str(e)}

            progresso[cidade_key]["status"] = "concluido"
            atualizar_scan_job(job_id,
                               processados=total_processados,
                               encontrados=total_encontrados,
                               erros=total_erros,
                               progresso=progresso)

        atualizar_scan_job(job_id,
                           status="concluido",
                           finished_at=datetime.now(),
                           processados=total_processados,
                           encontrados=total_encontrados,
                           erros=total_erros,
                           progresso=progresso)
        scan_log(job_id, f"Scan concluido! Processados: {total_processados}, "
                         f"Encontrados: {total_encontrados}, Erros: {total_erros}")

    except asyncio.CancelledError:
        atualizar_scan_job(job_id, status="cancelado",
                           finished_at=datetime.now())
        scan_log(job_id, "Scan cancelado pelo usuario.", "warning")
    except Exception as e:
        atualizar_scan_job(job_id, status="erro",
                           finished_at=datetime.now(),
                           erros=total_erros + 1)
        scan_log(job_id, f"Scan falhou: {e}", "error")
    finally:
        _scan_tasks.pop(job_id, None)


async def _etapa_maps_direcionado(job_id: int, cidade: str, uf: str,
                                    headless: bool) -> dict:
    """Executa busca Maps direcionada para uma cidade."""
    scan_log(job_id, f"[MAPS] Iniciando busca direcionada: {cidade}/{uf}")
    atualizar_scan_job(job_id, etapa_atual="maps")

    cnpjs = buscar_cnpjs_para_maps_direcionado(cidade, uf)
    if not cnpjs:
        scan_log(job_id, f"[MAPS] Nenhum CNPJ pendente em {cidade}/{uf}")
        return {"processados": 0, "encontrados": 0, "msg": "nenhum pendente"}

    scan_log(job_id, f"[MAPS] {len(cnpjs)} CNPJs para buscar em {cidade}/{uf}")

    # Importar scraper
    from gmaps_scraper import scrape_maps_direcionado

    encontrados = 0

    def on_match(dados_maps, dados_cnpj):
        nonlocal encontrados
        cnpj = dados_cnpj.get("cnpj", "")
        score = dados_maps.get("score_match", 0)
        atualizar_lead_maps(cnpj, dados_maps, score)
        encontrados += 1
        scan_log(job_id, f"[MAPS] Match: {cnpj} -> {dados_maps.get('nome', '')[:40]} (score={score:.2f})")

    stats = await scrape_maps_direcionado(cidade, uf, headless, callback=on_match)

    # Marcar CNPJs nao encontrados como checked
    total = stats.get("total", 0)
    scan_log(job_id, f"[MAPS] {cidade}/{uf}: {encontrados}/{total} encontrados")

    return {"processados": total, "encontrados": encontrados}


async def _etapa_delivery(job_id: int, cidade: str, uf: str,
                           plataforma: str, headless: bool) -> dict:
    """Executa verificacao delivery para uma cidade/plataforma."""
    config = DELIVERY_PLATAFORMAS.get(plataforma, {})
    nome_plat = config.get("nome", plataforma)

    scan_log(job_id, f"[{nome_plat}] Iniciando verificacao: {cidade}/{uf}")
    atualizar_scan_job(job_id, etapa_atual=plataforma)

    # Buscar leads pendentes
    if plataforma == "ifood":
        pendentes = obter_cnpjs_sem_ifood(cidade, uf)
    else:
        pendentes = obter_cnpjs_sem_delivery(cidade, uf, plataforma)

    if not pendentes:
        scan_log(job_id, f"[{nome_plat}] Todos leads de {cidade}/{uf} ja verificados")
        return {"processados": 0, "encontrados": 0, "msg": "todos verificados"}

    # Preparar items
    items = []
    for r in pendentes:
        nome = r.get("nome_maps") or r.get("nome_fantasia") or ""
        if not nome or nome == r.get("razao_social", ""):
            nome_maps = r.get("nome_maps") or ""
            if nome_maps:
                nome = nome_maps
            else:
                continue
        items.append({"id": r["cnpj"], "nome": nome, "cidade": cidade})

    if not items:
        scan_log(job_id, f"[{nome_plat}] Nenhum lead com nome valido em {cidade}/{uf}")
        return {"processados": 0, "encontrados": 0, "msg": "sem nomes validos"}

    scan_log(job_id, f"[{nome_plat}] {len(items)} leads para verificar")

    # Importar e executar delivery checker
    from delivery_checker import verificar_plataforma_batch

    resultados = await verificar_plataforma_batch(items, plataforma, headless)

    # Resultados ja foram salvos individualmente pelo _processar_micro_batch
    com = sum(1 for r in resultados if r["tem"])
    scan_log(job_id, f"[{nome_plat}] {cidade}/{uf}: {com}/{len(resultados)} encontrados")

    return {"processados": len(resultados), "encontrados": com}


def iniciar_scan_background(job_id: int, cidades: list, etapas: list,
                              headless: bool = True):
    """Inicia scan como task asyncio em background.
    Chamado pelo FastAPI handler."""
    loop = asyncio.get_event_loop()
    task = loop.create_task(
        executar_scan(job_id, cidades, etapas, headless)
    )
    _scan_tasks[job_id] = task
    return task


def cancelar_scan(job_id: int) -> bool:
    """Cancela scan ativo."""
    task = _scan_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
