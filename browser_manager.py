"""
browser_manager.py - Modulo central de gerenciamento de browser anti-deteccao
Reutilizavel por todos os scrapers (gmaps, delivery, ifood).

Fornece:
- criar_browser(): browser com fingerprint rotativo + stealth
- fechar_browser(): encerramento seguro
- gerar_pausa_break(): duracao ponderada (curta/media/longa)
- gerar_limite_pause_break(): itens antes do proximo break
- executar_pause_break(): fecha browser + espera + loga
- CircuitBreaker: pausa reativa por falhas consecutivas
- resetar_tab(): about:blank apos timeout
"""
import asyncio
import random

from config import (
    USER_AGENTS,
    PAUSE_BREAK_MIN_ITEMS, PAUSE_BREAK_MAX_ITEMS,
)
from logger import log

try:
    from playwright_stealth import Stealth
    _stealth_instance = Stealth()
    STEALTH_AVAILABLE = True
except ImportError:
    _stealth_instance = None
    STEALTH_AVAILABLE = False


# Viewports aleatorios para variar fingerprint (fonte unica)
VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 720},
]


async def criar_browser(pw, headless: bool = True):
    """Cria browser com fingerprint rotativo + stealth.
    Rotaciona: user_agent, viewport, locale.
    Aplica playwright-stealth se disponivel, senao init_script manual.

    Args:
        pw: instancia do async_playwright (ja iniciada)
        headless: modo headless

    Returns:
        tuple (browser, context, page)
    """
    user_agent = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)

    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            f"--window-size={viewport['width']},{viewport['height']}",
            "--disable-extensions",
            "--no-proxy-server",
        ],
    )

    context = await browser.new_context(
        user_agent=user_agent,
        viewport=viewport,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        geolocation={"latitude": -23.5505, "longitude": -46.6333},
        permissions=["geolocation"],
    )

    # Stealth: playwright-stealth se disponivel, senao fallback manual
    if STEALTH_AVAILABLE:
        await _stealth_instance.apply_stealth_async(context)
    else:
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

    return browser, context, page


async def fechar_browser(browser):
    """Fecha browser com tratamento de erro."""
    if browser:
        try:
            await browser.close()
        except Exception:
            pass


def gerar_pausa_break() -> float:
    """Gera duracao de pause break com distribuicao ponderada.
    55% curta (2-5 min), 30% media (8-15 min), 15% longa (20-35 min).
    Retorna segundos."""
    roll = random.random()
    if roll < 0.55:
        return random.uniform(2 * 60, 5 * 60)     # 2-5 min
    elif roll < 0.85:
        return random.uniform(8 * 60, 15 * 60)    # 8-15 min
    else:
        return random.uniform(20 * 60, 35 * 60)   # 20-35 min


def gerar_limite_pause_break() -> int:
    """Gera quantidade de itens antes do proximo pause break.
    Retorna inteiro aleatorio entre PAUSE_BREAK_MIN_ITEMS e PAUSE_BREAK_MAX_ITEMS."""
    return random.randint(PAUSE_BREAK_MIN_ITEMS, PAUSE_BREAK_MAX_ITEMS)


async def executar_pause_break(browser, log_prefix="[PAUSE]"):
    """Executa pause break: fecha browser, espera, loga.

    Args:
        browser: instancia do browser a fechar
        log_prefix: prefixo para log

    Returns:
        duracao em segundos
    """
    duracao = gerar_pausa_break()
    minutos = duracao / 60
    log.info(f"{log_prefix} Pause break: {minutos:.1f} min (simulando comportamento humano)")
    await fechar_browser(browser)
    await asyncio.sleep(duracao)
    return duracao


class CircuitBreaker:
    """Circuit breaker reativo para falhas consecutivas.
    Independente dos pause breaks preventivos."""

    def __init__(self, max_timeouts=3, max_erros=5):
        self.timeouts_consecutivos = 0
        self.erros_consecutivos = 0
        self.max_timeouts = max_timeouts   # 3 timeouts -> pausa curta
        self.max_erros = max_erros         # 5 erros -> pausa longa

    def registrar_sucesso(self):
        self.timeouts_consecutivos = 0
        self.erros_consecutivos = 0

    def registrar_timeout(self):
        self.timeouts_consecutivos += 1
        self.erros_consecutivos += 1

    def registrar_erro(self):
        self.erros_consecutivos += 1

    async def verificar(self, log_prefix="[CB]") -> str:
        """Verifica se precisa pausar. Retorna acao: 'ok', 'pausa_curta', 'pausa_longa'.
        Executa a pausa automaticamente se necessario."""
        if self.erros_consecutivos >= self.max_erros:
            pausa = random.uniform(5 * 60, 10 * 60)   # 5-10 min
            log.warning(f"{log_prefix} {self.erros_consecutivos} erros consecutivos "
                        f"— pausa {pausa / 60:.1f}min")
            await asyncio.sleep(pausa)
            self.erros_consecutivos = 0
            self.timeouts_consecutivos = 0
            return "pausa_longa"
        elif self.timeouts_consecutivos >= self.max_timeouts:
            pausa = random.uniform(60, 180)   # 1-3 min
            log.warning(f"{log_prefix} {self.timeouts_consecutivos} timeouts "
                        f"— pausa {pausa:.0f}s")
            await asyncio.sleep(pausa)
            self.timeouts_consecutivos = 0
            return "pausa_curta"
        return "ok"


async def resetar_tab(page, log_prefix="[RESET]"):
    """Navega para about:blank para resetar estado da tab apos timeout."""
    try:
        await page.goto("about:blank", timeout=5000)
        await asyncio.sleep(0.5)
    except Exception:
        pass
