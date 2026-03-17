"""
ifood_checker.py - Verificador de presenca no iFood
Usa a busca do iFood para verificar se um restaurante esta na plataforma.

v4.1: Micro-batches com browser restart + pause breaks via browser_manager.
"""
import asyncio
import random
import re
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from config import USER_AGENTS
from logger import log
from browser_manager import (
    criar_browser, fechar_browser,
    gerar_limite_pause_break, gerar_pausa_break,
)


async def verificar_ifood_batch(restaurantes: list, headless: bool = True) -> list:
    """
    Verifica presenca no iFood para uma lista de restaurantes.
    Usa micro-batches com browser restart e pause breaks.

    Args:
        restaurantes: Lista de dicts com 'id', 'nome', 'cidade'
        headless: Modo headless do browser

    Returns:
        Lista de dicts com resultado: {id, tem_ifood, ifood_nome, ifood_url}
    """
    resultados = []
    total = len(restaurantes)
    if total == 0:
        return resultados

    pw = None

    try:
        pw = await async_playwright().start()

        processados = 0

        while processados < total:
            itens_sessao = gerar_limite_pause_break()  # 30-50
            chunk_end = min(processados + itens_sessao, total)
            chunk = restaurantes[processados:chunk_end]

            browser = None
            try:
                browser, context, page = await criar_browser(pw, headless)

                for i, rest in enumerate(chunk):
                    resultado = {
                        "id": rest["id"],
                        "tem_ifood": False,
                        "ifood_nome": "",
                        "ifood_url": "",
                    }

                    try:
                        nome = rest["nome"]

                        # Normalizar nome para busca (remover caracteres especiais)
                        nome_busca = re.sub(r'[^\w\s]', '', nome).strip()

                        # URL de busca no iFood
                        url = f"https://www.ifood.com.br/busca?q={quote_plus(nome_busca)}"

                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(random.uniform(3, 6))

                        # Verificar se ha resultados de restaurantes
                        cards = page.locator('[data-card-type="MERCHANT"], a[href*="/delivery/"]')
                        count = await cards.count()

                        if count > 0:
                            for j in range(min(count, 5)):
                                try:
                                    card = cards.nth(j)
                                    card_text = (await card.text_content()).strip().lower()
                                    nome_lower = nome.lower()

                                    palavras_nome = nome_lower.split()
                                    matches = sum(1 for p in palavras_nome if p in card_text)

                                    if matches >= len(palavras_nome) * 0.5:
                                        resultado["tem_ifood"] = True

                                        try:
                                            nome_el = card.locator('span, h3').first
                                            resultado["ifood_nome"] = (await nome_el.text_content()).strip()
                                        except Exception:
                                            resultado["ifood_nome"] = nome

                                        try:
                                            href = await card.get_attribute("href")
                                            if href:
                                                resultado["ifood_url"] = (
                                                    f"https://www.ifood.com.br{href}"
                                                    if href.startswith("/") else href
                                                )
                                        except Exception:
                                            pass

                                        break
                                except Exception:
                                    continue

                        pos = processados + i + 1
                        status = "SIM" if resultado["tem_ifood"] else "NAO"
                        log.info(f"[iFood] ({pos}/{total}) {nome}: {status}")

                    except Exception as e:
                        pos = processados + i + 1
                        log.warning(f"[iFood WARN] ({pos}/{total}) {rest['nome']}: Erro - {e}")

                    resultados.append(resultado)

                    # Delay entre consultas
                    if (i + 1) % 5 == 0:
                        log.info(f"[iFood] Pausa de seguranca... ({processados + i + 1}/{total})")
                        await asyncio.sleep(random.uniform(8, 15))
                    else:
                        await asyncio.sleep(random.uniform(3, 6))

            except Exception as e:
                log.warning(f"[iFood] Erro na sessao: {e}")
            finally:
                await fechar_browser(browser)

            processados = chunk_end

            # Pause break entre sessoes (browser ja fechado)
            if processados < total:
                pausa = gerar_pausa_break()
                log.info(f"[iFood] Pause break: {pausa/60:.1f}min ({processados}/{total})")
                await asyncio.sleep(pausa)

    except Exception as e:
        log.error(f"[iFood ERRO] Falha geral: {e}")
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    return resultados


async def verificar_ifood_api(nome: str, cidade: str) -> dict:
    """
    Verificacao alternativa via API do iFood (quando disponivel).
    Fallback mais leve que o scraping.
    """
    import httpx

    resultado = {"tem_ifood": False, "ifood_nome": "", "ifood_url": ""}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
                "Platform": "Desktop",
            }

            params = {
                "term": nome,
                "size": 5,
            }

            resp = await client.get(
                "https://marketplace.ifood.com.br/v1/merchant-list/search",
                params=params,
                headers=headers,
            )

            if resp.status_code == 200:
                data = resp.json()
                merchants = data.get("merchants", [])

                for m in merchants:
                    m_nome = m.get("name", "").lower()
                    m_cidade = m.get("address", {}).get("city", "").lower()

                    if (nome.lower() in m_nome or m_nome in nome.lower()) and cidade.lower() in m_cidade:
                        resultado["tem_ifood"] = True
                        resultado["ifood_nome"] = m.get("name", "")
                        resultado["ifood_url"] = f"https://www.ifood.com.br/delivery/{m.get('slug', '')}"
                        break

    except Exception:
        pass

    return resultado
