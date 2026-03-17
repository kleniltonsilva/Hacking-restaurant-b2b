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
    GMAPS_DIRECTED_CONCURRENT_TABS, GMAPS_DIRECTED_DELAY_MIN,
    GMAPS_DIRECTED_DELAY_MAX, GMAPS_DIRECTED_MAX_RETRIES,
    GMAPS_DIRECTED_RETRY_BACKOFF, GMAPS_DIRECTED_SCORE_MINIMO,
    GMAPS_DIRECTED_TIMEOUT, BROWSER_SESSION_LIMIT,
)

# Score mínimo para fallback por nome (v4.2)
GMAPS_DIRECTED_NOME_SCORE_MINIMO = 0.70
from logger import log
from browser_manager import (
    criar_browser as bm_criar_browser, fechar_browser,
    gerar_limite_pause_break, gerar_pausa_break,
    CircuitBreaker, resetar_tab,
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
            "--no-proxy-server",
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
                log.info("[LOG] ✅ Modal de cookies aceito.")
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
        log.warning("[WARN] Lista de resultados não encontrada, tentando seletor alternativo...")
        feed_selector = '.m6QErb[aria-label]'
        try:
            await page.wait_for_selector(feed_selector, timeout=10000)
        except Exception:
            log.error("[ERRO] Não foi possível localizar a lista de resultados.")
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
                log.info(f"[LOG] 📋 Final da lista atingido após {i+1} scrolls.")
                break
        except Exception:
            pass
        
        # Contar resultados atuais
        count = await page.locator('div[role="feed"] > div > div > a[href*="maps/place"]').count()
        
        if count == ultimo_count:
            scrolls_sem_novos += 1
            if scrolls_sem_novos >= max_sem_novos:
                log.info(f"[LOG] 📋 Sem novos resultados após {max_sem_novos} scrolls. Total: {count}")
                break
        else:
            scrolls_sem_novos = 0
            if count % 20 == 0 or count != ultimo_count:
                log.info(f"[LOG] 🔄 Scroll {i+1}: {count} restaurantes carregados...")
        
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
        log.warning(f"[WARN] Erro ao extrair detalhes: {e}")
    
    return dados


async def scrape_restaurantes_cidade(cidade: str, uf: str, headless: bool = True,
                                      callback=None, save_callback=None,
                                      nomes_existentes: set = None) -> list:
    """
    Realiza varredura completa de restaurantes em uma cidade via Google Maps.

    Args:
        cidade: Nome da cidade
        uf: Sigla do estado
        headless: Se True, roda sem abrir janela do browser
        callback: Função chamada a cada restaurante extraído (para log em tempo real)
        save_callback: Função para salvar restaurante no DB imediatamente (progresso incremental)
        nomes_existentes: Set de nomes já no DB para pular (retomada de onde parou)

    Returns:
        Lista de dicts com dados dos restaurantes
    """
    termo_busca = f"restaurantes em {cidade} {uf}"
    url = f"https://www.google.com.br/maps/search/{quote_plus(termo_busca)}"

    log.info(f"[LOG] 🔍 Iniciando varredura: '{termo_busca}'")
    log.info(f"[LOG] 🌐 URL: {url}")

    resultados = []
    nomes_extraidos = set()  # Deduplicação dentro da sessão
    urls_extraidos = set()   # Deduplicação por URL
    if nomes_existentes:
        nomes_extraidos.update(nomes_existentes)
        log.info(f"[LOG] 📊 {len(nomes_existentes)} restaurantes já no DB (serão pulados)")

    pw = None
    browser = None

    try:
        pw, browser, context, page = await _criar_browser(headless)

        # Acessar Google Maps
        log.info("[LOG] 🚀 Acessando Google Maps...")
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        await _delay(3, 6)

        # Aceitar cookies
        await _aceitar_cookies(page)
        await _delay(2, 4)

        # Scroll para carregar todos os resultados
        log.info("[LOG] 📜 Iniciando scroll na lista de resultados...")
        total_carregados = await _scroll_lista_resultados(page)
        log.info(f"[LOG] ✅ Total de cards carregados: {total_carregados}")

        if total_carregados == 0:
            log.warning("[WARN] Nenhum resultado encontrado. Verifique o termo de busca.")
            return resultados

        # Extrair dados de cada card
        cards = page.locator('div[role="feed"] > div > div > a[href*="maps/place"]')
        total = await cards.count()

        log.info(f"[LOG] 🏪 Extraindo detalhes de {total} restaurantes...")

        # Pause break preventivo durante extracao
        proximo_pause_break = gerar_limite_pause_break()

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
                    nome_lower = dados["nome"].strip().lower()
                    maps_url = dados.get("google_maps_url", "")

                    # Deduplicação: pular se nome ou URL já foi extraído
                    if nome_lower in nomes_extraidos:
                        log.info(f"[LOG] ⏭️ Duplicata ignorada: {dados['nome']}")
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        continue
                    if maps_url and maps_url in urls_extraidos:
                        log.info(f"[LOG] ⏭️ Duplicata (URL) ignorada: {dados['nome']}")
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        continue

                    nomes_extraidos.add(nome_lower)
                    if maps_url:
                        urls_extraidos.add(maps_url)

                    resultados.append(dados)

                    # Salvar imediatamente no DB (progresso incremental)
                    if save_callback:
                        save_callback(dados)

                    # Log em tempo real
                    tel_info = f" | Tel: {dados['telefone']}" if dados['telefone'] else ""
                    web_info = f" | Web: {dados['website']}" if dados['website'] else ""
                    log.info(f"[+] ({i+1}/{total}) {dados['nome']}{tel_info}{web_info}")

                    if callback:
                        callback(dados)

                # Pause break preventivo (pausa longa sem fechar browser)
                if (i + 1) == proximo_pause_break:
                    pausa = min(gerar_pausa_break(), 5 * 60)  # Max 5 min dentro da sessao
                    log.info(f"[LOG] Pausa de seguranca estendida: {pausa/60:.1f}min ({i+1}/{total})")
                    await asyncio.sleep(pausa)
                    proximo_pause_break += gerar_limite_pause_break()
                # Delay entre extrações para não ser bloqueado
                elif (i + 1) % 10 == 0:
                    log.info(f"[LOG] ⏳ Pausa de segurança... ({i+1}/{total})")
                    await _delay(5, 10)
                else:
                    await _delay(1, 3)

                # Voltar para a lista (pressionar ESC ou clicar na lista)
                await page.keyboard.press("Escape")
                await asyncio.sleep(random.uniform(0.5, 1))

            except Exception as e:
                log.warning(f"[WARN] Erro no card {i+1}: {e}")
                # Tentar recuperar voltando para a lista
                try:
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(1)
                except Exception:
                    pass
                continue

        log.info(f"[SUCESSO] ✅ {cidade}/{uf}: {len(resultados)} restaurantes extraídos com sucesso!")

    except Exception as e:
        log.error(f"[ERRO] ❌ Falha na varredura de {cidade}/{uf}: {e}")

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
    
    log.info(f"[LOG] 🔍 Varredura rápida: '{termo_busca}'")
    
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
        
        log.info(f"[SUCESSO] ✅ {cidade}/{uf}: {len(resultados)} restaurantes (modo rápido)")

    except Exception as e:
        log.error(f"[ERRO] ❌ Falha: {e}")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    return resultados


# ============================================================
# BUSCA DIRECIONADA - Por endereço do CNPJ
# ============================================================

def _construir_queries_maps(cnpj_data: dict) -> list:
    """
    Gera queries de busca no Maps em ordem de prioridade.
    1. Nome fantasia + cidade + uf
    2. Endereço (logradouro + numero + cidade + uf)
    3. Nome fantasia + endereço + cidade
    """
    queries = []
    nome = cnpj_data.get("nome_fantasia", "") or ""
    razao = cnpj_data.get("razao_social", "") or ""
    logradouro = cnpj_data.get("logradouro", "") or ""
    numero = cnpj_data.get("numero", "") or ""
    cidade = cnpj_data.get("cidade", "") or ""
    uf = cnpj_data.get("uf", "") or ""

    # Cidade em title case para busca
    cidade_display = cidade.title() if cidade else ""

    # Query 1: nome fantasia (se diferente da razao social)
    if nome and nome.upper() != razao.upper():
        queries.append(f"{nome} {cidade_display} {uf}")

    # Query 2: endereco
    endereco_parts = [logradouro]
    if numero:
        endereco_parts.append(numero)
    endereco_str = " ".join(endereco_parts)
    if endereco_str.strip():
        queries.append(f"{endereco_str} {cidade_display} {uf}")

    # Query 3: nome + endereco combinado
    if nome and nome.upper() != razao.upper() and endereco_str.strip():
        queries.append(f"{nome} {endereco_str} {cidade_display}")

    # Fallback: razao social (sem sufixos juridicos)
    if not queries:
        razao_limpa = re.sub(r'\s*(LTDA|ME|EIRELI|S/?A|EPP|SLU|SS)\s*$', '', razao, flags=re.IGNORECASE).strip()
        if razao_limpa:
            queries.append(f"{razao_limpa} {cidade_display} {uf}")

    return queries


async def _extrair_detalhes_lugar(page) -> dict:
    """Extrai dados do lugar aberto no Maps (painel lateral)."""
    dados = {
        "nome": "", "endereco": "", "telefone": "", "website": "",
        "rating": "", "total_reviews": "", "categoria": "",
        "google_maps_url": "", "latitude": "", "longitude": "",
    }

    try:
        # URL atual (contém coordenadas)
        url = page.url
        dados["google_maps_url"] = url

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

        # Total de avaliacoes
        try:
            reviews_el = page.locator('div.F7nice span[aria-label*="avaliação"], div.F7nice span[aria-label*="review"]').first
            if await reviews_el.is_visible(timeout=2000):
                texto = (await reviews_el.text_content()).strip()
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

        # Endereco
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
        log.warning(f"[WARN] Erro ao extrair detalhes do lugar: {e}")

    return dados


async def _buscar_cnpj_no_maps(page, cnpj_data: dict) -> dict:
    """
    Busca um CNPJ especifico no Maps usando queries geradas.
    Tenta cada query, verifica score de endereco.
    v4.2: Fallback por nome quando score de endereco e baixo.
    Retorna dados do Maps + score ou {} se nao encontrou.
    """
    from address_matcher import calcular_score_endereco, calcular_score_nome

    queries = _construir_queries_maps(cnpj_data)
    cnpj = cnpj_data.get("cnpj", "???")
    logradouro = cnpj_data.get("logradouro", "") or ""
    numero = cnpj_data.get("numero", "") or ""
    bairro = cnpj_data.get("bairro", "") or ""
    cidade_cnpj = (cnpj_data.get("cidade", "") or "").upper()
    nome_fantasia = cnpj_data.get("nome_fantasia", "") or ""
    razao_social = cnpj_data.get("razao_social", "") or ""

    if not queries:
        log.warning(f"[MAPS-DIR] ⚠️ {cnpj}: sem queries possiveis")
        return {}

    # v4.2: Rastrear melhor candidato por nome durante o loop
    melhor_candidato_nome = None
    melhor_score_nome = 0.0

    def _verificar_cidade(dados):
        """Verifica se o resultado e da mesma cidade."""
        if not cidade_cnpj:
            return True
        endereco_upper = dados.get("endereco", "").upper()
        if cidade_cnpj.replace(" ", "") in endereco_upper.replace(" ", ""):
            return True
        cidade_title = cnpj_data.get("cidade", "").title()
        if cidade_title in dados.get("endereco", ""):
            return True
        return False

    def _avaliar_candidato_nome(dados):
        """Avalia um candidato para fallback por nome."""
        nonlocal melhor_candidato_nome, melhor_score_nome
        if not dados.get("nome"):
            return
        if not _verificar_cidade(dados):
            return
        score_n = calcular_score_nome(dados["nome"], nome_fantasia, razao_social)
        if score_n >= GMAPS_DIRECTED_NOME_SCORE_MINIMO and score_n > melhor_score_nome:
            melhor_score_nome = score_n
            melhor_candidato_nome = dados.copy()
            melhor_candidato_nome["score_nome"] = score_n

    for qi, query in enumerate(queries):
        try:
            search_url = f"https://www.google.com.br/maps/search/{quote_plus(query)}"
            await page.goto(search_url, wait_until="domcontentloaded",
                          timeout=GMAPS_DIRECTED_TIMEOUT)
            await asyncio.sleep(random.uniform(3, 6))

            # Detectar se caiu direto no lugar (URL tem /place/)
            current_url = page.url
            caiu_no_lugar = '/place/' in current_url

            if caiu_no_lugar:
                # Resultado unico - extrair diretamente
                dados = await _extrair_detalhes_lugar(page)
                if dados["nome"] and dados["endereco"]:
                    if not _verificar_cidade(dados):
                        log.info(f"[MAPS-DIR] ⏭️ {cnpj} q{qi+1}: resultado de outra cidade")
                        continue

                    score = calcular_score_endereco(
                        dados["endereco"], logradouro,
                        numero_receita=numero, bairro_receita=bairro
                    )
                    if score >= GMAPS_DIRECTED_SCORE_MINIMO:
                        dados["score_match"] = score
                        dados["match_tipo"] = "endereco"
                        log.info(f"[MAPS-DIR] 🟢 {cnpj}: {dados['nome'][:35]} (score={score:.2f}, q{qi+1})")
                        return dados
                    else:
                        log.info(f"[MAPS-DIR] 🟡 {cnpj} q{qi+1}: score baixo ({score:.2f}) - {dados['nome'][:30]}")
                        # v4.2: Avaliar como candidato por nome
                        _avaliar_candidato_nome(dados)
            else:
                # Lista de resultados - tentar os 3 primeiros
                await asyncio.sleep(random.uniform(1, 3))
                cards = page.locator('div[role="feed"] > div > div > a[href*="maps/place"]')
                total_cards = await cards.count()

                if total_cards == 0:
                    log.info(f"[MAPS-DIR] ⏭️ {cnpj} q{qi+1}: sem resultados")
                    continue

                melhor_dados = None
                melhor_score = 0.0

                for ci in range(min(total_cards, 3)):
                    try:
                        card = cards.nth(ci)
                        await card.scroll_into_view_if_needed()
                        await asyncio.sleep(random.uniform(0.5, 1))
                        await card.click()
                        await asyncio.sleep(random.uniform(2, 4))

                        dados = await _extrair_detalhes_lugar(page)

                        if dados["nome"] and dados["endereco"]:
                            score = calcular_score_endereco(
                                dados["endereco"], logradouro,
                                numero_receita=numero, bairro_receita=bairro
                            )
                            if score > melhor_score:
                                melhor_score = score
                                melhor_dados = dados.copy()
                                melhor_dados["score_match"] = score

                            # v4.2: Avaliar como candidato por nome
                            _avaliar_candidato_nome(dados)

                        # Voltar para lista
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(random.uniform(0.5, 1))

                    except Exception as e:
                        log.warning(f"[MAPS-DIR] ⚠️ {cnpj} q{qi+1} card{ci+1}: {e}")
                        try:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.5)
                        except Exception:
                            pass

                if melhor_dados and melhor_score >= GMAPS_DIRECTED_SCORE_MINIMO:
                    melhor_dados["match_tipo"] = "endereco"
                    log.info(f"[MAPS-DIR] 🟢 {cnpj}: {melhor_dados['nome'][:35]} (score={melhor_score:.2f}, q{qi+1})")
                    return melhor_dados
                elif melhor_score > 0:
                    log.info(f"[MAPS-DIR] 🟡 {cnpj} q{qi+1}: melhor score={melhor_score:.2f} (abaixo minimo)")

        except Exception as e:
            log.warning(f"[MAPS-DIR] ⚠️ {cnpj} q{qi+1}: {e}")

    # v4.2: Fallback por nome — aceitar candidato se endereco falhou
    if melhor_candidato_nome and melhor_score_nome >= GMAPS_DIRECTED_NOME_SCORE_MINIMO:
        melhor_candidato_nome["score_match"] = melhor_score_nome
        melhor_candidato_nome["match_tipo"] = "nome"
        log.info(f"[MAPS-DIR] 🔵 {cnpj}: FALLBACK POR NOME — {melhor_candidato_nome['nome'][:35]} "
                 f"(score_nome={melhor_score_nome:.2f})")
        return melhor_candidato_nome

    log.info(f"[MAPS-DIR] 🔴 {cnpj}: nao encontrado no Maps")
    return {}


async def _buscar_um_cnpj_maps(page, cnpj_data: dict, stats: dict,
                               circuit_breaker=None) -> dict:
    """Wrapper com retry para buscar um CNPJ no Maps.
    Sem semaphore — cada tab opera independente na sua page."""
    cnpj = cnpj_data.get("cnpj", "???")

    for tentativa in range(GMAPS_DIRECTED_MAX_RETRIES + 1):
        try:
            resultado = await _buscar_cnpj_no_maps(page, cnpj_data)

            if resultado:
                stats["encontrados"] += 1
                if circuit_breaker:
                    circuit_breaker.registrar_sucesso()
                return {"cnpj_data": cnpj_data, "maps_data": resultado}
            else:
                stats["nao_encontrados"] += 1
                if circuit_breaker:
                    circuit_breaker.registrar_sucesso()
                return {"cnpj_data": cnpj_data, "maps_data": {}}

        except Exception as e:
            is_timeout = "Timeout" in str(e) or "timeout" in str(e)
            if circuit_breaker:
                if is_timeout:
                    circuit_breaker.registrar_timeout()
                else:
                    circuit_breaker.registrar_erro()

            # Resetar tab apos timeout para evitar navegacao conflitante
            if is_timeout:
                await resetar_tab(page, "[MAPS-DIR]")

            if tentativa < GMAPS_DIRECTED_MAX_RETRIES:
                backoff = GMAPS_DIRECTED_RETRY_BACKOFF[tentativa]
                log.warning(f"[MAPS-DIR] {cnpj}: erro (tentativa {tentativa+1}/"
                            f"{GMAPS_DIRECTED_MAX_RETRIES+1}), retry em {backoff}s: {e}")
                await asyncio.sleep(backoff)
            else:
                log.error(f"[MAPS-DIR] {cnpj}: falhou apos "
                          f"{GMAPS_DIRECTED_MAX_RETRIES+1} tentativas: {e}")
                stats["erros"] += 1
                return {"cnpj_data": cnpj_data, "maps_data": {}}

    return {"cnpj_data": cnpj_data, "maps_data": {}}


async def scrape_maps_direcionado(cidade: str, uf: str, headless: bool = True,
                                   callback=None) -> dict:
    """
    Busca direcionada no Maps: para cada CNPJ detalhado com endereco,
    busca especificamente no Maps por aquele endereco.

    v4.1: Sessoes de BROWSER_SESSION_LIMIT (500) CNPJs com pause breaks,
    circuit breaker por tab, sem Semaphore (tabs independentes),
    timeout reduzido para 20s, resetar_tab apos timeout.

    Args:
        cidade: Nome da cidade
        uf: Sigla do estado
        headless: Se True, roda sem abrir janela
        callback: Funcao(dados_maps, dados_cnpj) chamada para cada match

    Returns:
        dict com stats: {total, encontrados, nao_encontrados, erros}
    """
    from playwright.async_api import async_playwright
    from db_manager import buscar_cnpjs_para_maps_direcionado

    cnpjs = buscar_cnpjs_para_maps_direcionado(cidade, uf)
    if not cnpjs:
        log.info(f"[MAPS-DIR] Nenhum CNPJ pendente para busca direcionada em {cidade}/{uf}")
        return {"total": 0, "encontrados": 0, "nao_encontrados": 0, "erros": 0}

    log.info(f"[MAPS-DIR] {len(cnpjs)} CNPJs para busca direcionada em {cidade}/{uf}")

    stats = {
        "total": len(cnpjs),
        "encontrados": 0,
        "nao_encontrados": 0,
        "erros": 0,
    }

    processados_global = 0

    # Loop de sessoes de browser (BROWSER_SESSION_LIMIT por sessao)
    while processados_global < len(cnpjs):
        lote_fim = min(processados_global + BROWSER_SESSION_LIMIT, len(cnpjs))
        lote = cnpjs[processados_global:lote_fim]
        sessao_num = processados_global // BROWSER_SESSION_LIMIT + 1

        log.info(f"[MAPS-DIR] === Sessao {sessao_num}: CNPJs {processados_global+1}-{lote_fim} "
                 f"de {len(cnpjs)} ===")

        pw = None
        browser = None

        try:
            pw = await async_playwright().start()
            browser, context, page = await bm_criar_browser(pw, headless)
            page.set_default_timeout(GMAPS_DIRECTED_TIMEOUT)

            # Aceitar cookies na primeira navegacao da sessao
            await page.goto("https://www.google.com.br/maps",
                            wait_until="domcontentloaded",
                            timeout=GMAPS_DIRECTED_TIMEOUT)
            await asyncio.sleep(random.uniform(3, 5))
            await _aceitar_cookies(page)
            await asyncio.sleep(random.uniform(1, 3))

            # Criar tabs adicionais
            pages = [page]
            for _ in range(GMAPS_DIRECTED_CONCURRENT_TABS - 1):
                new_page = await context.new_page()
                new_page.set_default_timeout(GMAPS_DIRECTED_TIMEOUT)
                pages.append(new_page)

            log.info(f"[MAPS-DIR] {len(pages)} tabs criadas")

            # Distribuir lote pelas tabs (round-robin)
            tab_queues = [[] for _ in range(len(pages))]
            for i, cnpj_data in enumerate(lote):
                tab_queues[i % len(pages)].append(cnpj_data)

            async def processar_fila(tab_page, fila, tab_idx):
                cb = CircuitBreaker()
                itens_ate_pausa = gerar_limite_pause_break()
                contador = 0

                for j, cnpj_data in enumerate(fila):
                    cnpj = cnpj_data.get("cnpj", "???")
                    pos = processados_global + sum(len(tab_queues[t]) for t in range(tab_idx)) + j + 1
                    log.info(f"[MAPS-DIR] ({pos}/{stats['total']}) Tab{tab_idx+1}: buscando {cnpj}...")

                    # Circuit breaker check
                    acao = await cb.verificar("[MAPS-DIR]")
                    if acao == "pausa_longa":
                        await resetar_tab(tab_page)

                    resultado = await _buscar_um_cnpj_maps(
                        tab_page, cnpj_data, stats, circuit_breaker=cb
                    )

                    if resultado and resultado.get("maps_data"):
                        maps_data = resultado["maps_data"]
                        maps_data["cidade"] = cidade
                        maps_data["uf"] = uf
                        if callback:
                            callback(maps_data, cnpj_data)

                    # Delay entre buscas
                    await _delay(GMAPS_DIRECTED_DELAY_MIN, GMAPS_DIRECTED_DELAY_MAX)

                    # Pause break por tab (pausa individual, sem fechar browser)
                    contador += 1
                    if contador >= itens_ate_pausa:
                        pausa = min(gerar_pausa_break(), 5 * 60)  # Max 5 min por tab
                        log.info(f"[MAPS-DIR] Tab{tab_idx+1}: pausa {pausa/60:.1f}min "
                                 f"({contador} itens processados)")
                        await asyncio.sleep(pausa)
                        contador = 0
                        itens_ate_pausa = gerar_limite_pause_break()

            # Executar todas as tabs em paralelo
            tasks = []
            for idx, (tab_page, fila) in enumerate(zip(pages, tab_queues)):
                if fila:
                    tasks.append(processar_fila(tab_page, fila, idx))

            await asyncio.gather(*tasks)

        except Exception as e:
            log.error(f"[MAPS-DIR] Erro na sessao {sessao_num}: {e}")
        finally:
            await fechar_browser(browser)
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass

        processados_global = lote_fim

        # PAUSE BREAK entre sessoes (browser ja fechado)
        if processados_global < len(cnpjs):
            duracao = gerar_pausa_break()
            log.info(f"[MAPS-DIR] Pause break entre sessoes: {duracao/60:.1f}min "
                     f"({processados_global}/{len(cnpjs)} processados)")
            await asyncio.sleep(duracao)

    log.info(f"[MAPS-DIR] === RESULTADO BUSCA DIRECIONADA {cidade}/{uf} ===")
    log.info(f"  Total CNPJs:        {stats['total']}")
    log.info(f"  Encontrados:        {stats['encontrados']}")
    log.info(f"  Nao encontrados:    {stats['nao_encontrados']}")
    log.info(f"  Erros:              {stats['erros']}")
    if stats['total'] > 0:
        taxa = stats['encontrados'] / stats['total'] * 100
        log.info(f"  Taxa de match:      {taxa:.1f}%")

    return stats
