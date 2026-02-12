"""
ifood_checker.py - Verificador de presença no iFood
Usa a busca do iFood para verificar se um restaurante está na plataforma.
"""
import asyncio
import random
import re
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from config import USER_AGENTS, IFOOD_SEARCH_URL


async def verificar_ifood_batch(restaurantes: list, headless: bool = True) -> list:
    """
    Verifica presença no iFood para uma lista de restaurantes.
    
    Args:
        restaurantes: Lista de dicts com 'id', 'nome', 'cidade'
        headless: Modo headless do browser
    
    Returns:
        Lista de dicts com resultado: {id, tem_ifood, ifood_nome, ifood_url}
    """
    resultados = []
    pw = None
    browser = None
    
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="pt-BR",
        )
        
        # Script anti-detecção
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        page = await context.new_page()
        
        total = len(restaurantes)
        
        for i, rest in enumerate(restaurantes):
            resultado = {
                "id": rest["id"],
                "tem_ifood": False,
                "ifood_nome": "",
                "ifood_url": "",
            }
            
            try:
                nome = rest["nome"]
                cidade = rest.get("cidade", "")
                
                # Normalizar nome para busca (remover caracteres especiais)
                nome_busca = re.sub(r'[^\w\s]', '', nome).strip()
                
                # URL de busca no iFood
                url = f"https://www.ifood.com.br/busca?q={quote_plus(nome_busca)}"
                
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(3, 6))
                
                # Verificar se há resultados de restaurantes
                # O iFood mostra cards de restaurantes nos resultados
                cards = page.locator('[data-card-type="MERCHANT"], a[href*="/delivery/"]')
                count = await cards.count()
                
                if count > 0:
                    # Verificar se algum resultado tem nome similar
                    for j in range(min(count, 5)):  # Checar os 5 primeiros
                        try:
                            card = cards.nth(j)
                            card_text = (await card.text_content()).strip().lower()
                            nome_lower = nome.lower()
                            
                            # Matching simples: nome do restaurante aparece no card
                            palavras_nome = nome_lower.split()
                            matches = sum(1 for p in palavras_nome if p in card_text)
                            
                            if matches >= len(palavras_nome) * 0.5:  # 50% das palavras batem
                                resultado["tem_ifood"] = True
                                
                                # Tentar pegar o nome exato no iFood
                                try:
                                    nome_el = card.locator('span, h3').first
                                    resultado["ifood_nome"] = (await nome_el.text_content()).strip()
                                except Exception:
                                    resultado["ifood_nome"] = nome
                                
                                # Pegar URL
                                try:
                                    href = await card.get_attribute("href")
                                    if href:
                                        resultado["ifood_url"] = f"https://www.ifood.com.br{href}" if href.startswith("/") else href
                                except Exception:
                                    pass
                                
                                break
                        except Exception:
                            continue
                
                status = "✅ SIM" if resultado["tem_ifood"] else "❌ NÃO"
                print(f"[iFood] ({i+1}/{total}) {nome}: {status}")
                
            except Exception as e:
                print(f"[iFood WARN] ({i+1}/{total}) {rest['nome']}: Erro - {e}")
            
            resultados.append(resultado)
            
            # Delay entre consultas
            if (i + 1) % 5 == 0:
                print(f"[iFood] ⏳ Pausa de segurança... ({i+1}/{total})")
                await asyncio.sleep(random.uniform(8, 15))
            else:
                await asyncio.sleep(random.uniform(3, 6))
    
    except Exception as e:
        print(f"[iFood ERRO] Falha geral: {e}")
    
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    
    return resultados


async def verificar_ifood_api(nome: str, cidade: str) -> dict:
    """
    Verificação alternativa via API do iFood (quando disponível).
    Fallback mais leve que o scraping.
    """
    import httpx
    
    resultado = {"tem_ifood": False, "ifood_nome": "", "ifood_url": ""}
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # API de busca do marketplace do iFood
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
