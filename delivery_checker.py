"""
delivery_checker.py - Verificacao multi-plataforma de delivery
v4.0 - Suporta iFood + Rappi + 99Food com micro-batches anti-Cloudflare

ESTRATEGIA:
- Micro-batches de 15-25 restaurantes
- Browser restart entre batches (novo fingerprint)
- Pausa 30-60s entre batches
- Delay 3-6s entre queries, 8-15s a cada 5
- Teste de acesso antes de iniciar (pula plataforma inacessivel)
"""
import asyncio
import random
import re
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from config import (
    USER_AGENTS, DELIVERY_PLATAFORMAS,
    DELIVERY_MICRO_BATCH_MIN, DELIVERY_MICRO_BATCH_MAX,
    DELIVERY_BATCH_COOLDOWN_MIN, DELIVERY_BATCH_COOLDOWN_MAX,
    DELIVERY_DELAY_MIN, DELIVERY_DELAY_MAX,
    DELIVERY_DELAY_LONG_MIN, DELIVERY_DELAY_LONG_MAX,
    DELIVERY_LONG_PAUSE_EVERY, DELIVERY_TIMEOUT,
    normalizar_cidade,
)
from logger import log
try:
    from db_pg import (
        obter_cnpjs_sem_delivery, atualizar_delivery_receita,
        obter_cnpjs_sem_ifood, atualizar_ifood_receita,
    )
except Exception:
    from receita_fetcher import (
        obter_cnpjs_sem_delivery, atualizar_delivery_receita,
        obter_cnpjs_sem_ifood, atualizar_ifood_receita,
    )
from browser_manager import (
    criar_browser, fechar_browser,
    gerar_limite_pause_break, gerar_pausa_break,
    VIEWPORTS,
)


async def _testar_acesso_plataforma(pw, plataforma: str) -> bool:
    """Testa se uma plataforma de delivery esta acessivel.
    Retorna True se acessivel, False se bloqueado/inacessivel."""
    config = DELIVERY_PLATAFORMAS.get(plataforma)
    if not config:
        return False

    # Testar URL principal
    urls_testar = [config["url_busca"].split("?")[0]]
    if config.get("url_alternativa"):
        urls_testar.append(config["url_alternativa"])

    browser = None
    try:
        browser, context, page = await criar_browser(pw, headless=True)

        for url in urls_testar:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded",
                                        timeout=DELIVERY_TIMEOUT)
                status = resp.status if resp else 0
                if status == 200:
                    await fechar_browser(browser)
                    return True
            except Exception:
                continue

        await fechar_browser(browser)
        browser = None
        return False

    except Exception:
        return False
    finally:
        await fechar_browser(browser)


async def _verificar_um_restaurante(page, nome: str, cidade: str,
                                      plataforma: str) -> dict:
    """Verifica presenca de um restaurante em uma plataforma de delivery.
    Retorna dict com tem_{plat}, nome, url."""
    config = DELIVERY_PLATAFORMAS.get(plataforma, {})
    resultado = {"tem": False, "nome_plat": "", "url_plat": ""}

    nome_busca = re.sub(r'[^\w\s]', '', nome).strip()
    url = config["url_busca"].format(query=quote_plus(nome_busca))

    try:
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=DELIVERY_TIMEOUT)
        await asyncio.sleep(random.uniform(2, 4))

        # Buscar cards de resultado
        cards = page.locator(config["seletores"])
        count = await cards.count()

        if count > 0:
            for j in range(min(count, 5)):
                try:
                    card = cards.nth(j)
                    card_text = (await card.text_content() or "").strip().lower()
                    nome_lower = nome.lower()

                    palavras_nome = nome_lower.split()
                    matches = sum(1 for p in palavras_nome if p in card_text)

                    if matches >= len(palavras_nome) * 0.5:
                        resultado["tem"] = True

                        try:
                            nome_el = card.locator(config.get("seletor_nome", "span, h3")).first
                            resultado["nome_plat"] = (await nome_el.text_content() or "").strip()
                        except Exception:
                            resultado["nome_plat"] = nome

                        try:
                            href = await card.get_attribute("href")
                            if href:
                                prefix = config.get("href_prefix", "")
                                resultado["url_plat"] = f"{prefix}{href}" if href.startswith("/") else href
                        except Exception:
                            pass

                        break
                except Exception:
                    continue

    except Exception as e:
        log.debug(f"[DELIVERY] Erro ao verificar {nome} no {config.get('nome', plataforma)}: {e}")

    return resultado


def _salvar_resultado_delivery(res: dict, plataforma: str):
    """Salva um resultado de delivery no banco imediatamente."""
    try:
        if plataforma == "ifood":
            atualizar_ifood_receita(
                res["id"], res["tem"],
                res.get("nome_plat", ""), res.get("url_plat", "")
            )
        else:
            atualizar_delivery_receita(
                res["id"], plataforma, res["tem"],
                res.get("nome_plat", ""), res.get("url_plat", "")
            )
    except Exception as e:
        log.warning(f"[DELIVERY] Erro ao salvar {res['id']}: {e}")


async def _processar_micro_batch(pw, items: list, plataforma: str,
                                   batch_num: int) -> list:
    """Processa um micro-batch de restaurantes em uma plataforma.
    Cria browser novo para cada micro-batch (anti-fingerprint).
    Salva cada resultado IMEDIATAMENTE no banco (resiliente a interrupcao)."""
    config = DELIVERY_PLATAFORMAS.get(plataforma, {})
    nome_plat = config.get("nome", plataforma)
    resultados = []
    browser = None

    try:
        browser, context, page = await criar_browser(pw, headless=True)
        total = len(items)

        for i, item in enumerate(items):
            res = await _verificar_um_restaurante(
                page, item["nome"], item["cidade"], plataforma
            )
            res["id"] = item["id"]

            status = "SIM" if res["tem"] else "NAO"
            log.info(f"[{nome_plat}] (batch {batch_num}, {i+1}/{total}) "
                     f"{item['nome'][:35]}: {status}")

            # Salvar IMEDIATAMENTE no banco (nao perde dados se interromper)
            _salvar_resultado_delivery(res, plataforma)

            resultados.append(res)

            # Delay entre queries
            if (i + 1) % DELIVERY_LONG_PAUSE_EVERY == 0:
                await asyncio.sleep(random.uniform(
                    DELIVERY_DELAY_LONG_MIN, DELIVERY_DELAY_LONG_MAX
                ))
            else:
                await asyncio.sleep(random.uniform(
                    DELIVERY_DELAY_MIN, DELIVERY_DELAY_MAX
                ))

        await page.close()

    except Exception as e:
        log.warning(f"[{nome_plat}] Micro-batch {batch_num} falhou: {e}")
    finally:
        await fechar_browser(browser)

    return resultados


async def verificar_plataforma_batch(items: list, plataforma: str,
                                       headless: bool = True) -> list:
    """Verifica presenca no delivery para uma lista de restaurantes.
    Usa micro-batches com browser restart para anti-deteccao.

    Args:
        items: Lista de dicts com 'id', 'nome', 'cidade'
        plataforma: 'ifood', 'rappi' ou '99food'
        headless: Modo headless

    Returns:
        Lista de dicts: {id, tem, nome_plat, url_plat}
    """
    config = DELIVERY_PLATAFORMAS.get(plataforma)
    if not config:
        log.error(f"[DELIVERY] Plataforma desconhecida: {plataforma}")
        return []

    nome_plat = config["nome"]
    total = len(items)
    if total == 0:
        return []

    resultados = []
    pw = None

    try:
        pw = await async_playwright().start()

        # Teste de acesso pre-batch
        log.info(f"[{nome_plat}] Testando acesso...")
        if not await _testar_acesso_plataforma(pw, plataforma):
            log.warning(f"[{nome_plat}] INACESSIVEL - pulando plataforma")
            return []
        log.info(f"[{nome_plat}] Acessivel - processando {total} restaurantes")

        processados = 0
        batch_num = 0
        itens_ate_pausa = gerar_limite_pause_break()
        itens_processados_sessao = 0

        while processados < total:
            batch_num += 1
            micro_size = random.randint(DELIVERY_MICRO_BATCH_MIN,
                                         DELIVERY_MICRO_BATCH_MAX)
            chunk_end = min(processados + micro_size, total)
            chunk = items[processados:chunk_end]

            log.info(f"[{nome_plat}] Micro-batch {batch_num}: "
                     f"{len(chunk)} restaurantes ({processados+1}-{chunk_end}/{total})")

            batch_results = await _processar_micro_batch(
                pw, chunk, plataforma, batch_num
            )
            resultados.extend(batch_results)
            processados = chunk_end
            itens_processados_sessao += len(chunk)

            # Pausa entre micro-batches (browser ja fechou no micro-batch)
            if processados < total:
                if itens_processados_sessao >= itens_ate_pausa:
                    # PAUSE BREAK longo (simular comportamento humano)
                    pausa = gerar_pausa_break()
                    log.info(f"[{nome_plat}] Pause break: {pausa/60:.1f}min "
                             f"({processados}/{total} processados)")
                    await asyncio.sleep(pausa)
                    itens_processados_sessao = 0
                    itens_ate_pausa = gerar_limite_pause_break()
                else:
                    # Cooldown normal entre micro-batches
                    pausa = random.uniform(DELIVERY_BATCH_COOLDOWN_MIN,
                                            DELIVERY_BATCH_COOLDOWN_MAX)
                    log.info(f"[{nome_plat}] Pausa entre batches: {pausa:.0f}s")
                    await asyncio.sleep(pausa)

    except Exception as e:
        log.error(f"[{nome_plat}] Falha geral: {e}")
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    com = sum(1 for r in resultados if r["tem"])
    log.info(f"[{nome_plat}] Resultado: {com}/{len(resultados)} encontrados")

    return resultados


def _preparar_items_delivery(cnpjs_pendentes: list, cidade: str) -> list:
    """Prepara lista de items para verificacao delivery a partir de CNPJs."""
    items = []
    pulados = 0
    for r in cnpjs_pendentes:
        nome = r.get("nome_maps") or r.get("nome_fantasia") or ""
        if not nome or nome == r.get("razao_social", ""):
            nome_maps = r.get("nome_maps") or ""
            if nome_maps:
                nome = nome_maps
            else:
                pulados += 1
                continue
        items.append({"id": r["cnpj"], "nome": nome, "cidade": cidade})
    if pulados > 0:
        log.info(f"[DELIVERY] {pulados} CNPJs pulados (sem nome fantasia e sem match Maps)")
    return items


async def verificar_delivery_cidade(cidade: str, uf: str,
                                      headless: bool = True,
                                      plataformas: list = None):
    """Verifica delivery multi-plataforma para todos os CNPJs de uma cidade.
    Plataformas padrao: todas configuradas (ifood, rappi, 99food).

    Args:
        cidade: nome da cidade
        uf: sigla do estado
        headless: modo headless
        plataformas: lista de plataformas a verificar (None=todas)
    """
    if plataformas is None:
        plataformas = list(DELIVERY_PLATAFORMAS.keys())

    for plat in plataformas:
        config = DELIVERY_PLATAFORMAS.get(plat)
        if not config:
            continue

        nome_plat = config["nome"]
        log.info(f"-- {nome_plat} --")

        # Buscar CNPJs pendentes para esta plataforma
        if plat == "ifood":
            # Compatibilidade: usar funcao existente
            cnpjs_pendentes = obter_cnpjs_sem_ifood(cidade, uf)
        else:
            cnpjs_pendentes = obter_cnpjs_sem_delivery(cidade, uf, plat)

        if not cnpjs_pendentes:
            log.info(f"[{nome_plat}] Todos CNPJs de {cidade}/{uf} ja verificados")
            continue

        items = _preparar_items_delivery(cnpjs_pendentes, cidade)
        if not items:
            log.warning(f"[{nome_plat}] Nenhum CNPJ com nome valido em {cidade}/{uf}")
            continue

        log.info(f"[{nome_plat}] {len(items)} restaurantes para verificar em {cidade}/{uf}")

        resultados = await verificar_plataforma_batch(items, plat, headless)

        # Resultados ja foram salvos individualmente em _processar_micro_batch
        com = sum(1 for r in resultados if r["tem"])
        log.info(f"[{nome_plat}] {cidade}/{uf}: {com}/{len(resultados)} encontrados")
