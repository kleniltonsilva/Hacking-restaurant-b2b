"""
main.py - Orquestrador Principal do Restaurant BI v4.0
Pipeline: Dados Abertos RF (Estab+Empresas+Simples+Socios) -> Maps -> Delivery

FLUXO (etapas manuais):
  [A] Importar Dados Abertos Receita Federal (completo: Estab+Empresas+Simples+Socios)
  [B] Busca Maps Direcionada (por endereco CNPJ)
  [C] Varredura Google Maps (generica - por cidade)
  [D] Cruzar Enderecos (Receita x Maps)
  [E] Verificar Delivery Multi-plataforma (iFood + Rappi + 99Food)

PIPELINES AUTOMATICOS:
  [P] Pipeline Maps (B+C+D+E + Exportacao) - sem RF
  [G] Pipeline Completo (A+B+C+D+E + Exportacao) - com RF
"""
import asyncio
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import CAPITAIS, STATUS_PENDENTE, STATUS_PROCESSADO, STATUS_IFOOD_CHECKED, normalizar_cidade
from init_db import init_database
from db_manager import (
    inserir_restaurante, inserir_restaurantes_batch, buscar_pendentes,
    buscar_por_cidade, atualizar_status, atualizar_ifood, atualizar_cnpj,
    inserir_socios, estatisticas_gerais, registrar_varredura,
    finalizar_varredura, cidades_ja_varridas, resetar_banco,
    nomes_restaurantes_cidade, inserir_restaurante_e_vincular,
)
from gmaps_scraper import scrape_restaurantes_cidade, scrape_cidade_simples, scrape_maps_direcionado
from ifood_checker import verificar_ifood_batch
from exporter import exportar_excel, exportar_csv
from receita_fetcher import (
    estatisticas_receita, init_tabela_receita,
    obter_cnpjs_sem_ifood, atualizar_ifood_receita,
)
from receita_federal import (
    importar_receita_federal,
    processar_complementares_standalone,
    selecionar_modo_importacao, selecionar_estado,
    selecionar_cidades_especificas,
)
from address_matcher import cruzar_cidade_completa, detectar_multi_restaurante, testar_similaridade
from logger import log


def limpar_tela():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║          RESTAURANT BI v4.0 - Prospeccao Inteligente            ║
║  RF Completo (Estab+Emp+Simples+Socios) x Maps x Multi-Delivery║
╠══════════════════════════════════════════════════════════════════╣
║  A -> B -> C -> D -> E  |  [P] Maps  [G] Completo               ║
╚══════════════════════════════════════════════════════════════════╝
    """)


def menu_principal():
    print("""
┌──────────────────────────────────────────────────────┐
│              MENU PRINCIPAL v4.0                      │
├──────────────────────────────────────────────────────┤
│  -- ETAPAS MANUAIS --                                │
│  [A] Importar RF Completo (Estab+Emp+Simples+Socios)│
│  [B] Busca Maps Direcionada (por endereco CNPJ)     │
│  [C] Varredura Maps (generica - por cidade)          │
│  [D] Cruzar Enderecos (Receita x Maps)              │
│  [E] Verificar Delivery (iFood+Rappi+99Food)        │
│  [R] Enriquecer RF (Empresas+Simples+Socios)        │
│                                                      │
│  -- AUTOMATICO --                                    │
│  [G] Pipeline Completo (A+B+C+D+E + Exportacao)     │
│  [P] Pipeline Maps (B+C+D+E + Exportacao)            │
│                                                      │
│  -- CONSULTAS --                                     │
│  [5] Consultar Banco    [6] Exportar Excel           │
│  [7] Exportar CSV       [8] Estatisticas             │
│  [T] Testar Algoritmo   [9] Resetar                  │
│  [0] Sair                                            │
└──────────────────────────────────────────────────────┘
    """)
    return input("Escolha: ").strip().upper()


def selecionar_cidade() -> tuple:
    print("\n┌── SELECIONAR CIDADE ──┐")
    print("│  [0] Todas as capitais │")

    varridas = {(v["cidade"], v["uf"]) for v in cidades_ja_varridas()}
    stats_receita = {}
    try:
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT cidade, uf, COUNT(*) as total FROM cnpjs_receita GROUP BY cidade, uf"
        ).fetchall()
        for row in rows:
            stats_receita[(row["cidade"], row["uf"])] = row["total"]
        conn.close()
    except Exception:
        pass

    for i, cap in enumerate(CAPITAIS, 1):
        marca_maps = " ✅" if (cap["cidade"], cap["uf"]) in varridas else ""
        qtd = stats_receita.get((normalizar_cidade(cap["cidade"]), cap["uf"]), 0)
        marca_rec = f" 🏛️{qtd}" if qtd > 0 else ""
        print(f"│  [{i:2d}] {cap['cidade']}/{cap['uf']}{marca_maps}{marca_rec}")

    print("└────────────────────────┘")
    print("  ✅=Maps  🏛️N=CNPJs Receita")

    escolha = input("\nEscolha: ").strip()
    if escolha == "0":
        return None, None
    try:
        idx = int(escolha) - 1
        if 0 <= idx < len(CAPITAIS):
            return CAPITAIS[idx]["cidade"], CAPITAIS[idx]["uf"]
    except ValueError:
        pass
    print("[ERRO] Opção inválida.")
    return "__INVALIDO__", ""


def selecionar_modo_scraping() -> str:
    print("\n[1] 🐢 Completo (clica cada restaurante)")
    print("[2] 🐇 Rápido (extrai da lista)")
    return input("Modo (1/2): ").strip()


# ============================================================
# AÇÕES
# ============================================================

async def acao_importar_receita_federal():
    """Importa dados abertos da Receita Federal (Estabelecimentos)."""
    modo_str = selecionar_modo_importacao()

    if modo_str == "1":
        modo = "capitais"
        cidades_alvo = None
        uf_alvo = None
    elif modo_str == "2":
        modo = "estado"
        uf_alvo = selecionar_estado()
        if not uf_alvo:
            return
        cidades_alvo = None
    elif modo_str == "3":
        modo = "cidades"
        cidades_alvo = selecionar_cidades_especificas()
        if not cidades_alvo:
            print("[ERRO] Nenhuma cidade informada.")
            return
        uf_alvo = None
    else:
        print("[ERRO] Opcao invalida.")
        return

    manter = input("\nManter arquivos ZIP apos processar? [s/N]: ").strip().lower() == "s"
    forcar = input("Forcar re-download? [s/N]: ").strip().lower() == "s"

    if input("\n🚀 Iniciar importacao? [S/n]: ").strip().lower() == "n":
        return

    stats = await importar_receita_federal(
        modo=modo,
        cidades_alvo=cidades_alvo,
        uf_alvo=uf_alvo,
        manter_arquivos=manter,
        forcar_download=forcar,
    )

    log.info(f"\n✅ Importacao concluida! {stats['inseridos']:,} CNPJs importados.")
    input("\n[ENTER para continuar...]")


async def acao_verificar_delivery():
    """Verifica delivery multi-plataforma (iFood + Rappi + 99Food).
    v4.0: usa delivery_checker.py com micro-batches."""
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return

    # Selecionar plataformas
    from config import DELIVERY_PLATAFORMAS
    print("\n  Plataformas disponiveis:")
    plats = list(DELIVERY_PLATAFORMAS.keys())
    for i, p in enumerate(plats, 1):
        nome = DELIVERY_PLATAFORMAS[p]["nome"]
        print(f"  [{i}] {nome}")
    print(f"  [0] Todas (Recomendado)")

    escolha = input("Escolha: ").strip()
    if escolha == "0" or not escolha:
        plataformas = None  # todas
    else:
        try:
            idx = int(escolha) - 1
            if 0 <= idx < len(plats):
                plataformas = [plats[idx]]
            else:
                plataformas = None
        except ValueError:
            plataformas = None

    headless = input("Headless? [S/n]: ").strip().lower() != "n"
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]

    try:
        from delivery_checker import verificar_delivery_cidade
        for c, u in cidades:
            log.info(f"{'='*60}")
            log.info(f"  DELIVERY MULTI-PLATAFORMA: {c}/{u}")
            log.info(f"{'='*60}")
            await verificar_delivery_cidade(c, u, headless, plataformas)
    except ImportError:
        # Fallback: iFood-only
        log.warning("[DELIVERY] delivery_checker.py nao encontrado - usando iFood-only")
        for c, u in cidades:
            cnpjs_pendentes = obter_cnpjs_sem_ifood(c, u)
            if not cnpjs_pendentes:
                log.info(f"[iFood] Todos CNPJs de {c}/{u} ja verificados no iFood.")
                continue
            items = []
            for r in cnpjs_pendentes:
                nome = r.get("nome_maps") or r.get("nome_fantasia") or ""
                if not nome or nome == r.get("razao_social", ""):
                    continue
                items.append({"id": r["cnpj"], "nome": nome, "cidade": c})
            if items:
                resultados = await verificar_ifood_batch(items, headless)
                for res in resultados:
                    atualizar_ifood_receita(
                        res["id"], res["tem_ifood"], res["ifood_nome"], res["ifood_url"]
                    )
                com = sum(1 for r in resultados if r["tem_ifood"])
                log.info(f"[iFood] {com}/{len(resultados)} no iFood em {c}/{u}")

    input("\n[ENTER para continuar...]")


async def acao_enriquecer_rf_complementares():
    """Enriquece CNPJs existentes com dados complementares da RF (Empresas+Simples+Socios)."""
    import sqlite3
    from config import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM cnpjs_receita").fetchone()[0]
    enriquecidos = conn.execute(
        "SELECT COUNT(*) FROM cnpjs_receita WHERE enriquecido_rf = 1"
    ).fetchone()[0]
    com_socios = conn.execute(
        "SELECT COUNT(*) FROM cnpjs_receita WHERE socios_json IS NOT NULL AND socios_json != '[]'"
    ).fetchone()[0]
    conn.close()

    print(f"""
┌──────────────────────────────────────────────────────┐
│       ENRIQUECER RF (Empresas+Simples+Socios)        │
├──────────────────────────────────────────────────────┤
│  CNPJs no banco:     {total:>10,}                     │
│  Ja enriquecidos:    {enriquecidos:>10,}                     │
│  Com socios:         {com_socios:>10,}                     │
└──────────────────────────────────────────────────────┘

  Baixa ~230MB (Empresas+Simples+Socios) e atualiza:
  - Razao social, capital social, porte, natureza juridica
  - Simples Nacional, MEI
  - Socios (QSA completo)
    """)

    if total == 0:
        print("[ERRO] Nenhum CNPJ no banco. Importe Estabelecimentos primeiro (opcao A).")
        input("\n[ENTER para continuar...]")
        return

    manter = input("Manter arquivos ZIP apos processar? [s/N]: ").strip().lower() == "s"
    forcar = input("Forcar re-download? [s/N]: ").strip().lower() == "s"

    if input("\nIniciar enriquecimento? [S/n]: ").strip().lower() == "n":
        return

    stats = await processar_complementares_standalone(manter, forcar)

    if stats:
        log.info(f"\n  Resumo do enriquecimento:")
        if "empresas" in stats and stats["empresas"]:
            log.info(f"    Empresas: {stats['empresas'].get('atualizados', 0):,} atualizados")
        if "simples" in stats and stats["simples"]:
            log.info(f"    Simples:  {stats['simples'].get('atualizados', 0):,} atualizados")
        if "socios" in stats and stats["socios"]:
            log.info(f"    Socios:   {stats['socios'].get('cnpjs_com_socios', 0):,} com socios")
        if "marcados_detalhado" in stats:
            log.info(f"    Detalhado=1: {stats['marcados_detalhado']:,} CNPJs")

    input("\n[ENTER para continuar...]")


async def acao_varredura():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    modo = selecionar_modo_scraping()
    headless_input = input("\nHeadless? [S/n]: ").strip().lower()
    headless = headless_input != "n"
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        log.info(f"{'='*60}")
        log.info(f"  🔍 GOOGLE MAPS: {c}/{u}")
        log.info(f"{'='*60}")
        registrar_varredura(c, u)
        nomes_ja = nomes_restaurantes_cidade(c, u)
        if modo == "2":
            resultados = await scrape_cidade_simples(c, u, headless)
            if resultados:
                inseridos = inserir_restaurantes_batch(resultados)
                log.info(f"[DB] 💾 {inseridos} novos (de {len(resultados)} encontrados)")
        else:
            resultados = await scrape_restaurantes_cidade(
                c, u, headless,
                save_callback=inserir_restaurante,
                nomes_existentes=nomes_ja,
            )
            if resultados:
                log.info(f"[DB] 💾 {len(resultados)} restaurantes extraídos (salvos incrementalmente)")
        if resultados:
            restaurantes_db = buscar_por_cidade(c, u)
            for r in restaurantes_db:
                if r["status"] == STATUS_PENDENTE:
                    atualizar_status(r["id"], STATUS_PROCESSADO)
            finalizar_varredura(c, u, len(resultados))
        else:
            log.warning(f"[WARN] Nenhum restaurante em {c}/{u}")
    input("\n[ENTER para continuar...]")


async def acao_maps_direcionado():
    """Busca direcionada no Maps: busca cada CNPJ detalhado pelo seu endereço."""
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    headless = input("\nHeadless? [S/n]: ").strip().lower() != "n"
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]

    for c, u in cidades:
        log.info(f"{'='*60}")
        log.info(f"  🎯 BUSCA MAPS DIRECIONADA: {c}/{u}")
        log.info(f"{'='*60}")

        def on_match(dados_maps, dados_cnpj):
            dados_cnpj["score_match"] = dados_maps.get("score_match", 0)
            rest_id = inserir_restaurante_e_vincular(dados_maps, dados_cnpj)
            if rest_id:
                log.info(f"[DB] 💾 Vinculado: {dados_cnpj.get('cnpj', '')} -> restaurante #{rest_id}")

        stats = await scrape_maps_direcionado(c, u, headless, callback=on_match)

        if stats["total"] > 0:
            log.info(f"[MAPS-DIR] ✅ {c}/{u}: {stats['encontrados']}/{stats['total']} encontrados")
        else:
            log.info(f"[MAPS-DIR] ℹ️ Nenhum CNPJ pendente em {c}/{u}")

    input("\n[ENTER para continuar...]")


async def acao_cruzar_enderecos():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        log.info(f"{'='*60}")
        log.info(f"  🔗 CRUZAMENTO: {c}/{u}")
        log.info(f"  (Só restaurantes sem CNPJ)")
        log.info(f"{'='*60}")
        cruzar_cidade_completa(c, u)
        try:
            detectar_multi_restaurante(c, u)
        except Exception as e:
            log.warning(f"[MATCH] ⚠️ Multi-restaurante: {e}")
    input("\n[ENTER para continuar...]")


def _selecionar_geografia_pipeline() -> tuple:
    """Seleciona modo geografico para o pipeline.
    Retorna (cidades_lista, modo_rf, descricao) ou (None, None, None) se cancelado.
    cidades_lista = lista de (cidade, uf)
    modo_rf = modo para importar_receita_federal se necessario
    """
    print("""
┌──────────────────────────────────────────────────────┐
│           SELECAO GEOGRAFICA - PIPELINE              │
├──────────────────────────────────────────────────────┤
│  [1] Capital especifica                              │
│  [2] Por estado (todas cidades)                      │
│  [3] Cidades especificas (Cidade/UF, Cidade/UF)      │
│  [4] Todas as capitais (27 cidades)                  │
└──────────────────────────────────────────────────────┘""")
    escolha = input("Escolha (1/2/3/4): ").strip()

    if escolha == "1":
        cidade, uf = selecionar_cidade()
        if cidade == "__INVALIDO__" or cidade is None:
            if cidade is None:
                # Selecionou "todas" - redirecionar para opcao 4
                return [(c["cidade"], c["uf"]) for c in CAPITAIS], "capitais", "Todas as capitais"
            return None, None, None
        return [(cidade, uf)], "cidades", f"{cidade}/{uf}"

    elif escolha == "2":
        uf = selecionar_estado()
        if not uf:
            return None, None, None
        # Buscar cidades deste estado que tem CNPJs no banco
        try:
            import sqlite3
            from config import DB_PATH
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT DISTINCT cidade, uf FROM cnpjs_receita WHERE uf = ? ORDER BY cidade",
                (uf.upper(),)
            ).fetchall()
            conn.close()
            if rows:
                cidades = [(row["cidade"], row["uf"]) for row in rows]
                log.info(f"[PIPELINE] {len(cidades)} cidades de {uf} com dados RF")
            else:
                # Sem dados no banco - importar por estado
                cidades = []
        except Exception:
            cidades = []
        return cidades, "estado", f"Estado {uf}"

    elif escolha == "3":
        cidades = selecionar_cidades_especificas()
        if not cidades:
            return None, None, None
        return cidades, "cidades", f"{len(cidades)} cidades especificas"

    elif escolha == "4":
        return [(c["cidade"], c["uf"]) for c in CAPITAIS], "capitais", "Todas as capitais"

    else:
        print("[ERRO] Opcao invalida.")
        return None, None, None


async def _executar_pipeline_cidade(c: str, u: str, headless: bool):
    """Executa B+C+D+E para uma cidade (sempre modo hibrido).
    B: Maps Direcionada -> C: Maps Generica -> D: Cruzamento -> E: Delivery."""
    log.info(f"{'='*70}")
    log.info(f"  PIPELINE v4.0: {c}/{u}")
    log.info(f"{'='*70}")

    st_m = {"matched": 0, "taxa": "N/A"}

    def on_match_pipeline(dados_maps, dados_cnpj):
        dados_cnpj["score_match"] = dados_maps.get("score_match", 0)
        inserir_restaurante_e_vincular(dados_maps, dados_cnpj)

    # B: Busca Maps Direcionada (por endereco CNPJ)
    log.info(f"-- B: Maps Direcionada (por endereco CNPJ) --")
    st_maps = await scrape_maps_direcionado(c, u, headless, callback=on_match_pipeline)
    st_m = {
        "matched": st_maps.get("encontrados", 0),
        "taxa": f"{st_maps['encontrados']/st_maps['total']*100:.1f}%" if st_maps.get("total", 0) > 0 else "N/A",
    }

    # C: Busca Maps Generica (cobertura ampla)
    log.info(f"-- C: Maps Generica (cobertura ampla) --")
    registrar_varredura(c, u)
    nomes_ja = nomes_restaurantes_cidade(c, u)
    res = await scrape_restaurantes_cidade(
        c, u, headless,
        save_callback=inserir_restaurante,
        nomes_existentes=nomes_ja,
    )
    if res:
        log.info(f"[MAPS] {len(res)} restaurantes genericos (salvos incrementalmente)")
        finalizar_varredura(c, u, len(res))
        for r in buscar_por_cidade(c, u):
            if r["status"] == STATUS_PENDENTE:
                atualizar_status(r["id"], STATUS_PROCESSADO)

    # D: Cruzamento de Enderecos
    log.info(f"-- D: Cruzamento Enderecos --")
    st_cruz = cruzar_cidade_completa(c, u)
    st_m["matched"] = st_m.get("matched", 0) + st_cruz.get("matched", 0)

    # Detectar socios multi-restaurante
    try:
        detectar_multi_restaurante(c, u)
    except Exception as e:
        log.warning(f"[MATCH] Multi-restaurante: {e}")

    # E: Delivery multi-plataforma (iFood + Rappi + 99Food)
    log.info(f"-- E: Delivery Multi-plataforma --")
    try:
        from delivery_checker import verificar_delivery_cidade
        await verificar_delivery_cidade(c, u, headless)
    except ImportError:
        # Fallback: iFood-only (compatibilidade)
        log.warning("[DELIVERY] delivery_checker.py nao encontrado - usando iFood-only")
        try:
            cnpjs_ifood = obter_cnpjs_sem_ifood(c, u)
            if cnpjs_ifood:
                items = []
                pulados = 0
                resgatados_maps = 0
                for r in cnpjs_ifood:
                    nome = r.get("nome_maps") or r.get("nome_fantasia") or ""
                    if not nome or nome == r.get("razao_social", ""):
                        nome_maps = r.get("nome_maps") or ""
                        if nome_maps:
                            nome = nome_maps
                            resgatados_maps += 1
                        else:
                            pulados += 1
                            continue
                    items.append({"id": r["cnpj"], "nome": nome, "cidade": c})
                if items:
                    res_if = await verificar_ifood_batch(items, headless)
                    for r in res_if:
                        atualizar_ifood_receita(r["id"], r["tem_ifood"], r["ifood_nome"], r["ifood_url"])
                    com = sum(1 for r in res_if if r["tem_ifood"])
                    log.info(f"[iFood] {com}/{len(res_if)} no iFood")
            else:
                log.info(f"[iFood] Todos ja verificados")
        except Exception as e:
            log.warning(f"[iFood] {e}")
    except Exception as e:
        log.warning(f"[DELIVERY] {e}")

    log.info(f"[PIPELINE] {c}/{u} PRONTO! Match: {st_m.get('matched', 0)} ({st_m.get('taxa', 'N/A')})")


async def acao_pipeline_maps():
    """Pipeline Maps [P]: B+C+D+E + Exportacao (sem importacao RF)."""
    cidades_lista, modo_rf, descricao = _selecionar_geografia_pipeline()
    if cidades_lista is None:
        return

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              PIPELINE MAPS v4.0                              ║
║  B: Direcionada + C: Generica + D: Cruzamento + E: Delivery ║
║  Escopo: {descricao:<51}║
╚══════════════════════════════════════════════════════════════╝""")

    # Verificar se tem dados RF (aviso informativo)
    cidades_sem_dados = []
    for c, u in cidades_lista:
        st_rec = estatisticas_receita(normalizar_cidade(c), u)
        if st_rec['total'] == 0:
            cidades_sem_dados.append((c, u))

    if cidades_sem_dados:
        print(f"\n[WARN] {len(cidades_sem_dados)} cidade(s) sem dados RF importados.")
        print(f"  A busca direcionada [B] depende de CNPJs da Receita.")
        print(f"  Use [G] Pipeline Completo ou [A] Importar RF primeiro.")
        if input("Continuar mesmo assim? [S/n]: ").strip().lower() == "n":
            return

    headless = input("Headless? [S/n]: ").strip().lower() != "n"

    print(f"\n  {len(cidades_lista)} cidades para processar (B+C+D+E)")
    if input("Iniciar? [S/n]: ").strip().lower() == "n":
        return

    for c, u in cidades_lista:
        await _executar_pipeline_cidade(c, u, headless)

    # Exportar
    log.info(f"-- EXPORTACAO --")
    if len(cidades_lista) == 1:
        exportar_excel(cidades_lista[0][0], cidades_lista[0][1])
    else:
        exportar_excel()
    input("\n[ENTER para continuar...]")


async def acao_pipeline_completo():
    """Pipeline Completo [G]: A+B+C+D+E + Exportacao (com importacao RF)."""
    cidades_lista, modo_rf, descricao = _selecionar_geografia_pipeline()
    if cidades_lista is None:
        return

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              PIPELINE COMPLETO v4.0                          ║
║  A: RF -> B: Direcionada -> C: Generica -> D: Cruz -> E: Del║
║       Estab + Empresas + Simples + Socios                   ║
║  Escopo: {descricao:<51}║
╚══════════════════════════════════════════════════════════════╝""")

    # A: Importar dados RF
    cidades_sem_dados = []
    cidades_com_dados = []
    for c, u in cidades_lista:
        st_rec = estatisticas_receita(normalizar_cidade(c), u)
        if st_rec['total'] == 0:
            cidades_sem_dados.append((c, u))
        else:
            cidades_com_dados.append((c, u))

    if cidades_sem_dados:
        if len(cidades_sem_dados) == len(cidades_lista):
            print(f"\nNenhum CNPJ importado para as cidades selecionadas!")
        else:
            print(f"\n{len(cidades_sem_dados)} cidades sem dados RF: "
                  f"{', '.join(f'{c}/{u}' for c, u in cidades_sem_dados[:5])}"
                  f"{'...' if len(cidades_sem_dados) > 5 else ''}")
        if input("Importar dados RF agora? [S/n]: ").strip().lower() != "n":
            log.info(f"-- A: Importacao Receita Federal --")
            if modo_rf == "capitais":
                await importar_receita_federal(modo='capitais')
            elif modo_rf == "estado":
                uf_alvo = cidades_sem_dados[0][1] if cidades_sem_dados else cidades_lista[0][1]
                await importar_receita_federal(modo='estado', uf_alvo=uf_alvo)
            else:
                await importar_receita_federal(modo='cidades', cidades_alvo=cidades_sem_dados)
            # Recarregar lista de cidades com dados (estado pode ter gerado novas cidades)
            if modo_rf == "estado":
                try:
                    import sqlite3
                    from config import DB_PATH
                    conn = sqlite3.connect(DB_PATH)
                    conn.row_factory = sqlite3.Row
                    uf_alvo = cidades_lista[0][1]
                    rows = conn.execute(
                        "SELECT DISTINCT cidade, uf FROM cnpjs_receita WHERE uf = ? ORDER BY cidade",
                        (uf_alvo.upper(),)
                    ).fetchall()
                    conn.close()
                    cidades_lista = [(row["cidade"], row["uf"]) for row in rows]
                except Exception:
                    pass
        else:
            if not cidades_com_dados:
                return
            cidades_lista = cidades_com_dados
    else:
        log.info(f"[RF] Todas as cidades ja tem dados RF importados.")

    headless = input("Headless? [S/n]: ").strip().lower() != "n"

    print(f"\n  {len(cidades_lista)} cidades para processar (B+C+D+E)")
    if input("Iniciar? [S/n]: ").strip().lower() == "n":
        return

    for c, u in cidades_lista:
        await _executar_pipeline_cidade(c, u, headless)

    # Exportar
    log.info(f"-- EXPORTACAO --")
    if len(cidades_lista) == 1:
        exportar_excel(cidades_lista[0][0], cidades_lista[0][1])
    else:
        exportar_excel()
    input("\n[ENTER para continuar...]")


def acao_consultar():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    if not cidade:
        stats = estatisticas_gerais()
        stats_rec = estatisticas_receita()
        print(f"\n{'='*70}")
        print(f"  RESUMO: {stats['total']} restaurantes | "
              f"iFood: {stats['com_ifood']} | CNPJ: {stats['com_cnpj']} | "
              f"Sócios: {stats['total_socios']}")
        print(f"  Receita: {stats_rec['total']} CNPJs | "
              f"Detalhados: {stats_rec['detalhados']} | "
              f"Matched: {stats_rec['matched']} | "
              f"Tel Prop: {stats_rec.get('com_tel_proprietario', 0)}")
        if stats['por_cidade']:
            print(f"\n  {'Cidade':<20} {'Maps':<7} {'iFood':<7} {'CNPJ':<7} {'TelProp':<7}")
            print(f"  {'─'*51}")
            for c in stats['por_cidade']:
                print(f"  {c['cidade']:<20} {c['total']:<7} {c['com_ifood']:<7} {c['com_cnpj']:<7} {c.get('com_tel_prop', 0):<7}")
        input("\n[ENTER para continuar...]")
        return

    dados = buscar_por_cidade(cidade, uf)
    if not dados:
        log.info(f"[INFO] Nenhum restaurante em {cidade}/{uf}.")
        input("\n[ENTER...]")
        return

    st = estatisticas_receita(normalizar_cidade(cidade), uf)
    print(f"\n  {cidade}/{uf}: {len(dados)} Maps | {st['total']} Receita | {st['matched']} matched")
    for i, d in enumerate(dados[:50], 1):
        ic = "🛵" if d["tem_ifood"] else "  "
        cn = "📋" if d.get("cnpj") else "  "
        print(f"  {i:3d}. {ic}{cn} {d['nome'][:40]:<40} ⭐{d.get('rating','N/A')}")
    if len(dados) > 50:
        print(f"  ... +{len(dados)-50} restaurantes")
    input("\n[ENTER...]")


def acao_estatisticas():
    stats = estatisticas_gerais()
    stats_rec = estatisticas_receita()
    print(f"""
╔════════════════════════════════════════════════════════╗
║               📈 ESTATÍSTICAS v3.1                    ║
╠════════════════════════════════════════════════════════╣
║  ── Google Maps ──                                    ║
║  Restaurantes: {stats['total']:>6}  Pendentes: {stats['pendentes']:>6}           ║
║  iFood: {stats['com_ifood']:>6}        CNPJ: {stats['com_cnpj']:>6}              ║
║  Sócios: {stats['total_socios']:>6}   Tel Prop: {stats.get('com_tel_proprietario', 0):>6}           ║
║                                                       ║
║  ── Receita Federal ──                                ║
║  CNPJs base: {stats_rec['total']:>6}   Detalhados: {stats_rec['detalhados']:>6}        ║
║  Matched: {stats_rec['matched']:>6}      Email: {stats_rec['com_email']:>6}             ║
║  Tel Prop: {stats_rec.get('com_tel_proprietario', 0):>6}    iFood: {stats_rec.get('com_ifood', 0):>6}             ║
╚════════════════════════════════════════════════════════╝""")
    input("\n[ENTER...]")


def acao_resetar():
    print("\n⚠️  APAGAR TUDO (incluindo Receita)?")
    if input("Digite 'RESETAR': ").strip() == "RESETAR":
        resetar_banco()
        print("✅ Resetado.")
    else:
        print("Cancelado.")
    input("\n[ENTER...]")


# ============================================================
# LOOP
# ============================================================

async def main():
    init_database()
    while True:
        limpar_tela()
        banner()
        op = menu_principal()
        try:
            if op == "A": await acao_importar_receita_federal()
            elif op == "B": await acao_maps_direcionado()
            elif op == "C": await acao_varredura()
            elif op == "D": await acao_cruzar_enderecos()
            elif op == "E": await acao_verificar_delivery()
            elif op == "R": await acao_enriquecer_rf_complementares()
            elif op == "G": await acao_pipeline_completo()
            elif op == "P": await acao_pipeline_maps()
            elif op == "5": acao_consultar()
            elif op == "6":
                c, u = selecionar_cidade()
                if c != "__INVALIDO__":
                    p = exportar_excel(c, u) if c else exportar_excel()
                    if p: print(f"📁 {p}")
                    input("\n[ENTER...]")
            elif op == "7":
                c, u = selecionar_cidade()
                if c != "__INVALIDO__":
                    p = exportar_csv(c, u) if c else exportar_csv()
                    if p: print(f"📁 {p}")
                    input("\n[ENTER...]")
            elif op == "8": acao_estatisticas()
            elif op == "T":
                testar_similaridade()
                input("\n[ENTER...]")
            elif op == "9": acao_resetar()
            elif op == "0":
                print("\n👋 Até mais!")
                sys.exit(0)
            else:
                print("[ERRO] Opção inválida.")
                input("\n[ENTER...]")
        except KeyboardInterrupt:
            log.info("[INFO] Interrompido. Dados salvos.")
            input("[ENTER para voltar ao menu...]")
        except Exception as e:
            log.error(f"[ERRO] {e}")
            import traceback
            traceback.print_exc()
            input("[ENTER...]")


if __name__ == "__main__":
    asyncio.run(main())
