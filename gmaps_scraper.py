"""
gmaps_scraper.py - Scraper stealth do Google Maps via Playwright
Extrai restaurantes com: Nome, Endereço, Telefone, Website, Rating, Categoria
"""
import asyncio
import random
import re
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, Page, Browser

from config import (
    USER_AGENTS, MIN_DELAY, MAX_DELAY,
    SCROLL_DELAY_MIN, SCROLL_DELAY_MAX,
    MAX_SCROLLS, PAGE_TIMEOUT,
)


async def _delay(min_s: float = None, max_s: float = None):
    """Delay randômico para simular comportamento humano."""
    mn = min_s or MIN_DELAY
    mx = max_s or MAX_DELAY
    wait = random.uniform(mn, mx)
    await asyncio.sleep(wait)


async def _short_delay():
    """Delay curto entre ações menores."""
    await asyncio.sleep(random.uniform(SCROLL_DELAY_MIN, SCROLL_DELAY_MAX))


async def _criar_browser(headless: bool = True) -> tuple:
    """Cria browser com configurações stealth."""
    pw = await async_playwright().start()
    
    user_agent = random.choice(USER_AGENTS)
    
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-size=1920,1080",
            "--disable-extensions",
        ]
    )
    
    context = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1920, "height": 1080},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        geolocation={"latitude": -23.5505, "longitude": -46.6333},
        permissions=["geolocation"],
    )
    
    # Injetar script anti-detecção
    await context.add_init_script("""
        // Remover flag de webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
        // Simular plugins reais
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Chrome runtime
        window.chrome = { runtime: {} };
        
        // Permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    """)
    
    page = await context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    
    return pw, browser, context, page


async def _aceitar_cookies(page: Page):
    """Tenta aceitar modais de cookies/privacidade do Google."""
    seletores_aceitar = [
        'button:has-text("Aceitar tudo")',
        'button:has-text("Accept all")',
        'button:has-text("Concordo")',
        'button:has-text("I agree")',
        '[aria-label="Aceitar tudo"]',
        '[aria-label="Accept all"]',
        'form[action*="consent"] button',
        '.VfPpkd-LgbsSe[data-mdc-dialog-action="accept"]',
    ]
    
    for seletor in seletores_aceitar:
        try:
            btn = page.locator(seletor).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                print("[LOG] ✅ Modal de cookies aceito.")
                await _short_delay()
                return True
        except Exception:
            continue
    return False


async def _scroll_lista_resultados(page: Page) -> int:
    """
    Faz scroll infinito na lista lateral de resultados do Google Maps.
    Retorna o número total de resultados encontrados.
    """
    # Localizar o container da lista de resultados
    feed_selector = 'div[role="feed"]'
    
    try:
        await page.wait_for_selector(feed_selector, timeout=15000)
    except Exception:
        print("[WARN] Lista de resultados não encontrada, tentando seletor alternativo...")
        feed_selector = '.m6QErb[aria-label]'
        try:
            await page.wait_for_selector(feed_selector, timeout=10000)
        except Exception:
            print("[ERRO] Não foi possível localizar a lista de resultados.")
            return 0
    
    ultimo_count = 0
    scrolls_sem_novos = 0
    max_sem_novos = 5  # Para se nada novo aparecer por 5 scrolls
    
    for i in range(MAX_SCROLLS):
        # Scroll dentro do feed
        await page.evaluate(f"""
            const feed = document.querySelector('{feed_selector}');
            if (feed) feed.scrollTop = feed.scrollHeight;
        """)
        
        await _short_delay()
        
        # Verificar se chegou ao final da lista
        try:
            end_marker = page.locator('span.HlvSq, p.fontBodyMedium:has-text("Você chegou ao final")')
            if await end_marker.is_visible(timeout=1000):
                print(f"[LOG] 📋 Final da lista atingido após {i+1} scrolls.")
                break
        except Exception:
            pass
        
        # Contar resultados atuais
        count = await page.locator('div[role="feed"] > div > div > a[href*="maps/place"]').count()
        
        if count == ultimo_count:
            scrolls_sem_novos += 1
            if scrolls_sem_novos >= max_sem_novos:
                print(f"[LOG] 📋 Sem novos resultados após {max_sem_novos} scrolls. Total: {count}")
                break
        else:
            scrolls_sem_novos = 0
            if count % 20 == 0 or count != ultimo_count:
                print(f"[LOG] 🔄 Scroll {i+1}: {count} restaurantes carregados...")
        
        ultimo_count = count
    
    return ultimo_count


async def _extrair_detalhes_card(page: Page, card_element) -> dict:
    """Extrai detalhes de um card individual na lista do Google Maps."""
    dados = {
        "nome": "",
        "endereco": "",
        "telefone": "",
        "website": "",
        "rating": "",
        "total_reviews": "",
        "categoria": "",
        "google_maps_url": "",
        "latitude": "",
        "longitude": "",
    }
    
    try:
        # Clicar no card para abrir detalhes
        await card_element.click()
        await asyncio.sleep(random.uniform(2, 4))
        
        # URL atual (contém coordenadas)
        url = page.url
        dados["google_maps_url"] = url
        
        # Extrair coordenadas da URL
        coord_match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if coord_match:
            dados["latitude"] = coord_match.group(1)
            dados["longitude"] = coord_match.group(2)
        
        # Nome
        try:
            nome_el = page.locator('h1.DUwDvf, h1.fontHeadlineLarge').first
            if await nome_el.is_visible(timeout=3000):
                dados["nome"] = (await nome_el.text_content()).strip()
        except Exception:
            pass
        
        # Rating
        try:
            rating_el = page.locator('div.F7nice span[aria-hidden="true"]').first
            if await rating_el.is_visible(timeout=2000):
                dados["rating"] = (await rating_el.text_content()).strip()
        except Exception:
            pass
        
        # Total de avaliações
        try:
            reviews_el = page.locator('div.F7nice span[aria-label*="avaliação"], div.F7nice span[aria-label*="review"]').first
            if await reviews_el.is_visible(timeout=2000):
                texto = (await reviews_el.text_content()).strip()
                # Extrair número: "(1.234)" -> "1234"
                num = re.sub(r'[^\d]', '', texto)
                dados["total_reviews"] = num
        except Exception:
            pass
        
        # Categoria
        try:
            cat_el = page.locator('button.DkEaL, span.DkEaL').first
            if await cat_el.is_visible(timeout=2000):
                dados["categoria"] = (await cat_el.text_content()).strip()
        except Exception:
            pass
        
        # Endereço
        try:
            addr_el = page.locator('[data-item-id="address"] .fontBodyMedium, button[data-item-id="address"]').first
            if await addr_el.is_visible(timeout=2000):
                dados["endereco"] = (await addr_el.text_content()).strip()
        except Exception:
            pass
        
        # Telefone
        try:
            phone_el = page.locator('[data-item-id*="phone"] .fontBodyMedium, button[data-item-id*="phone"]').first
            if await phone_el.is_visible(timeout=2000):
                texto = (await phone_el.text_content()).strip()
                # Limpar telefone
                telefone_limpo = re.sub(r'[^\d()+\- ]', '', texto)
                dados["telefone"] = telefone_limpo.strip()
        except Exception:
            pass
        
        # Website
        try:
            web_el = page.locator('[data-item-id="authority"] .fontBodyMedium, a[data-item-id="authority"]').first
            if await web_el.is_visible(timeout=2000):
                dados["website"] = (await web_el.text_content()).strip()
        except Exception:
            pass
        
    except Exception as e:
        print(f"[WARN] Erro ao extrair detalhes: {e}")
    
    return dados


async def scrape_restaurantes_cidade(cidade: str, uf: str, headless: bool = True,
                                      callback=None) -> list:
    """
    Realiza varredura completa de restaurantes em uma cidade via Google Maps.
    
    Args:
        cidade: Nome da cidade
        uf: Sigla do estado
        headless: Se True, roda sem abrir janela do browser
        callback: Função chamada a cada restaurante extraído (para log em tempo real)
    
    Returns:
        Lista de dicts com dados dos restaurantes
    """
    termo_busca = f"restaurantes em {cidade} {uf}"
    url = f"https://www.google.com.br/maps/search/{quote_plus(termo_busca)}"
    
    print(f"\n[LOG] 🔍 Iniciando varredura: '{termo_busca}'")
    print(f"[LOG] 🌐 URL: {url}")
    
    resultados = []
    pw = None
    browser = None
    
    try:
        pw, browser, context, page = await _criar_browser(headless)
        
        # Acessar Google Maps
        print("[LOG] 🚀 Acessando Google Maps...")
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        await _delay(3, 6)
        
        # Aceitar cookies
        await _aceitar_cookies(page)
        await _delay(2, 4)
        
        # Scroll para carregar todos os resultados
        print("[LOG] 📜 Iniciando scroll na lista de resultados...")
        total_carregados = await _scroll_lista_resultados(page)
        print(f"[LOG] ✅ Total de cards carregados: {total_carregados}")
        
        if total_carregados == 0:
            print("[WARN] Nenhum resultado encontrado. Verifique o termo de busca.")
            return resultados
        
        # Extrair dados de cada card
        cards = page.locator('div[role="feed"] > div > div > a[href*="maps/place"]')
        total = await cards.count()
        
        print(f"[LOG] 🏪 Extraindo detalhes de {total} restaurantes...")
        
        for i in range(total):
            try:
                card = cards.nth(i)
                
                # Garantir que o card está visível (scroll até ele)
                await card.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.5, 1.5))
                
                dados = await _extrair_detalhes_card(page, card)
                dados["cidade"] = cidade
                dados["uf"] = uf
                
                if dados["nome"]:
                    resultados.append(dados)
                    
                    # Log em tempo real
                    tel_info = f" | Tel: {dados['telefone']}" if dados['telefone'] else ""
                    web_info = f" | Web: {dados['website']}" if dados['website'] else ""
                    print(f"[+] ({i+1}/{total}) {dados['nome']}{tel_info}{web_info}")
                    
                    if callback:
                        callback(dados)
                
                # Delay entre extrações para não ser bloqueado
                if (i + 1) % 10 == 0:
                    print(f"[LOG] ⏳ Pausa de segurança... ({i+1}/{total})")
                    await _delay(5, 10)
                else:
                    await _delay(1, 3)
                
                # Voltar para a lista (pressionar ESC ou clicar na lista)
                await page.keyboard.press("Escape")
                await asyncio.sleep(random.uniform(0.5, 1))
                
            except Exception as e:
                print(f"[WARN] Erro no card {i+1}: {e}")
                # Tentar recuperar voltando para a lista
                try:
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(1)
                except Exception:
                    pass
                continue
        
        print(f"\n[SUCESSO] ✅ {cidade}/{uf}: {len(resultados)} restaurantes extraídos com sucesso!")
        
    except Exception as e:
        print(f"[ERRO] ❌ Falha na varredura de {cidade}/{uf}: {e}")
    
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    
    return resultados


async def scrape_cidade_simples(cidade: str, uf: str, headless: bool = True) -> list:
    """
    Versão simplificada que extrai dados básicos direto dos cards sem clicar em cada um.
    Mais rápida, mas com menos dados (sem telefone/website individuais).
    Útil para uma primeira passada rápida.
    """
    termo_busca = f"restaurantes em {cidade} {uf}"
    url = f"https://www.google.com.br/maps/search/{quote_plus(termo_busca)}"
    
    print(f"\n[LOG] 🔍 Varredura rápida: '{termo_busca}'")
    
    resultados = []
    pw = None
    browser = None
    
    try:
        pw, browser, context, page = await _criar_browser(headless)
        
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        await _delay(3, 6)
        await _aceitar_cookies(page)
        await _delay(2, 4)
        
        total_carregados = await _scroll_lista_resultados(page)
        
        if total_carregados == 0:
            return resultados
        
        # Extrair dados básicos via JavaScript (muito mais rápido)
        dados_brutos = await page.evaluate("""
            () => {
                const results = [];
                const cards = document.querySelectorAll('div[role="feed"] > div > div > a[href*="maps/place"]');
                
                cards.forEach(card => {
                    const nome_el = card.querySelector('.fontHeadlineSmall, .qBF1Pd');
                    const rating_el = card.querySelector('.MW4etd');
                    const reviews_el = card.querySelector('.UY7F9');
                    const tipo_el = card.querySelector('.W4Efsd:nth-child(1) > .W4Efsd span:not(.MW4etd):not(.UY7F9)');
                    const endereco_el = card.querySelector('.W4Efsd:nth-child(2) > .W4Efsd:nth-child(1) span:nth-child(2)');
                    const info_el = card.querySelector('.W4Efsd:nth-child(2) > .W4Efsd:nth-child(2)');
                    
                    const nome = nome_el ? nome_el.textContent.trim() : '';
                    const href = card.getAttribute('href') || '';
                    
                    if (nome) {
                        results.push({
                            nome: nome,
                            rating: rating_el ? rating_el.textContent.trim() : '',
                            total_reviews: reviews_el ? reviews_el.textContent.replace(/[^0-9]/g, '') : '',
                            categoria: tipo_el ? tipo_el.textContent.trim() : '',
                            endereco: endereco_el ? endereco_el.textContent.trim() : '',
                            google_maps_url: href,
                        });
                    }
                });
                
                return results;
            }
        """)
        
        for d in dados_brutos:
            d["cidade"] = cidade
            d["uf"] = uf
            d["telefone"] = ""
            d["website"] = ""
            d["latitude"] = ""
            d["longitude"] = ""
            
            # Extrair coordenadas da URL se disponível
            coord_match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', d.get("google_maps_url", ""))
            if coord_match:
                d["latitude"] = coord_match.group(1)
                d["longitude"] = coord_match.group(2)
            
            resultados.append(d)
        
        print(f"[SUCESSO] ✅ {cidade}/{uf}: {len(resultados)} restaurantes (modo rápido)")
        
    except Exception as e:
        print(f"[ERRO] ❌ Falha: {e}")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    
    return resultados
