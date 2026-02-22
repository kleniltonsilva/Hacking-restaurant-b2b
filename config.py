"""
config.py - Configurações globais do Restaurant BI
"""
import os
import unicodedata


def normalizar_cidade(nome: str) -> str:
    """Normaliza nome de cidade: UPPER + remove acentos.
    Essencial para comparar com banco onde cidades estão sem acento."""
    if not nome:
        return ""
    upper = nome.upper().strip()
    nfkd = unicodedata.normalize('NFKD', upper)
    return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))

# ============================================================
# DIRETÓRIOS
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
DB_PATH = os.path.join(DATA_DIR, "restaurants.db")

# Criar diretórios se não existirem
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# ============================================================
# SCRAPING - CONFIGURAÇÕES STEALTH
# ============================================================
# Delays randômicos (em segundos) entre ações
MIN_DELAY = 8
MAX_DELAY = 20

# Delay entre scroll na lista do Google Maps
SCROLL_DELAY_MIN = 2
SCROLL_DELAY_MAX = 5

# Máximo de scrolls na lista lateral (cada scroll carrega ~7-10 resultados)
MAX_SCROLLS = 80

# Timeout para carregamento de páginas (ms)
PAGE_TIMEOUT = 60000

# User-Agents reais e atualizados
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
]

# ============================================================
# APIs
# ============================================================
BRASIL_API_BASE = "https://brasilapi.com.br/api"
BRASIL_API_CNPJ = f"{BRASIL_API_BASE}/cnpj/v2"

# iFood - URL base para busca
IFOOD_SEARCH_URL = "https://www.ifood.com.br/busca"

# cnpj.biz - Detalhamento com telefone do proprietário
CNPJBIZ_URL = "https://cnpj.biz"
CNPJBIZ_DELAY_MIN = 5
CNPJBIZ_DELAY_MAX = 12
CNPJBIZ_TIMEOUT = 45000

# cnpj.biz - Retry e concorrência (v3.1)
CNPJBIZ_MAX_RETRIES = 3
CNPJBIZ_RETRY_BACKOFF = [5, 10, 20]  # segundos entre retries
CNPJBIZ_CONCURRENT_TABS = 7
CNPJBIZ_REVEAL_WAIT = 4  # segundos após revealAllContacts()
CNPJBIZ_CLOUDFLARE_PAUSE_MIN = 30  # pausa quando detecta bloqueio
CNPJBIZ_CLOUDFLARE_PAUSE_MAX = 60

# Google Maps - Busca direcionada por CNPJ
GMAPS_DIRECTED_CONCURRENT_TABS = 5
GMAPS_DIRECTED_DELAY_MIN = 5
GMAPS_DIRECTED_DELAY_MAX = 12
GMAPS_DIRECTED_MAX_RETRIES = 2
GMAPS_DIRECTED_RETRY_BACKOFF = [5, 15]
GMAPS_DIRECTED_SCORE_MINIMO = 0.50
GMAPS_DIRECTED_TIMEOUT = 45000

# Diretório de logs
LOGS_DIR = os.path.join(BASE_DIR, "Logs_secoes")

# ============================================================
# CAPITAIS BRASILEIRAS (ordenadas por relevância comercial)
# ============================================================
CAPITAIS = [
    {"cidade": "São Paulo", "uf": "SP", "ddd": "11"},
    {"cidade": "Rio de Janeiro", "uf": "RJ", "ddd": "21"},
    {"cidade": "Belo Horizonte", "uf": "MG", "ddd": "31"},
    {"cidade": "Curitiba", "uf": "PR", "ddd": "41"},
    {"cidade": "Porto Alegre", "uf": "RS", "ddd": "51"},
    {"cidade": "Salvador", "uf": "BA", "ddd": "71"},
    {"cidade": "Brasília", "uf": "DF", "ddd": "61"},
    {"cidade": "Fortaleza", "uf": "CE", "ddd": "85"},
    {"cidade": "Recife", "uf": "PE", "ddd": "81"},
    {"cidade": "Goiânia", "uf": "GO", "ddd": "62"},
    {"cidade": "Belém", "uf": "PA", "ddd": "91"},
    {"cidade": "Manaus", "uf": "AM", "ddd": "92"},
    {"cidade": "Vitória", "uf": "ES", "ddd": "27"},
    {"cidade": "Florianópolis", "uf": "SC", "ddd": "48"},
    {"cidade": "Natal", "uf": "RN", "ddd": "84"},
    {"cidade": "Campo Grande", "uf": "MS", "ddd": "67"},
    {"cidade": "São Luís", "uf": "MA", "ddd": "98"},
    {"cidade": "Maceió", "uf": "AL", "ddd": "82"},
    {"cidade": "João Pessoa", "uf": "PB", "ddd": "83"},
    {"cidade": "Teresina", "uf": "PI", "ddd": "86"},
    {"cidade": "Cuiabá", "uf": "MT", "ddd": "65"},
    {"cidade": "Aracaju", "uf": "SE", "ddd": "79"},
    {"cidade": "Porto Velho", "uf": "RO", "ddd": "69"},
    {"cidade": "Macapá", "uf": "AP", "ddd": "96"},
    {"cidade": "Boa Vista", "uf": "RR", "ddd": "95"},
    {"cidade": "Rio Branco", "uf": "AC", "ddd": "68"},
    {"cidade": "Palmas", "uf": "TO", "ddd": "63"},
]

# ============================================================
# STATUS DOS REGISTROS NO BANCO
# ============================================================
STATUS_PENDENTE = "pendente"
STATUS_PROCESSADO = "processado"       # Google Maps extraído
STATUS_IFOOD_CHECKED = "ifood_checked"  # iFood verificado
STATUS_ENRIQUECIDO = "enriquecido"     # CNPJ + Sócios obtidos
STATUS_ERRO = "erro"

# ============================================================
# RECEITA FEDERAL - DADOS ABERTOS
# ============================================================
# Mirror Casa dos Dados (URL oficial da RF mudou em jan/2026)
RECEITA_FEDERAL_URL = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/"
# URL para descobrir a pasta mais recente
RECEITA_FEDERAL_INDEX_URL = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/"
RECEITA_FEDERAL_DIR = os.path.join(DATA_DIR, "receita_federal")
os.makedirs(RECEITA_FEDERAL_DIR, exist_ok=True)

# CNAEs de restaurante/alimentação
CNAES_RESTAURANTE = {"5611201", "5611202", "5611203", "5612100"}

# Número de arquivos de Estabelecimentos (0 a 9)
NUM_ARQUIVOS_ESTABELECIMENTOS = 10

# UFs brasileiras (para seleção por estado)
UFS_BRASIL = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
    "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
    "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]
