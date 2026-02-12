"""
simulacao.py - Simulação completa do Restaurant BI
Demonstra o fluxo real do sistema com dados fictícios realistas de Curitiba/PR.
Mostra exatamente o que o usuário veria no terminal ao rodar o sistema.
"""
import time
import random
import os
import sqlite3
from datetime import datetime

# ============================================================
# CONFIGURAÇÃO DA SIMULAÇÃO
# ============================================================
CIDADE = "Curitiba"
UF = "PR"
DB_SIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "simulacao.db")
os.makedirs(os.path.dirname(DB_SIM), exist_ok=True)

# Dados fictícios realistas de restaurantes de Curitiba
RESTAURANTES_SIMULADOS = [
    {
        "nome": "Madero Steak House - Batel",
        "endereco": "Av. do Batel, 1440 - Batel, Curitiba - PR, 80420-090",
        "telefone": "(41) 3023-8080",
        "website": "www.madero.com.br",
        "rating": "4.6",
        "total_reviews": "8432",
        "categoria": "Hamburgueria",
        "latitude": "-25.4372",
        "longitude": "-49.2894",
        "google_maps_url": "https://maps.google.com/?cid=madero_batel",
        "tem_ifood": True,
        "ifood_nome": "Madero - Batel",
        "cnpj": "10.786.940/0001-78",
        "razao_social": "MADERO INDUSTRIA E COMERCIO S.A.",
        "nome_fantasia": "MADERO",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2009-04-01",
        "capital_social": 500000000.00,
        "socios": [
            {"nome": "JUNIOR DURSKI", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "CARLYLE PARTNERS VII", "qualificacao": "Sócio", "tipo": "PJ"},
        ],
    },
    {
        "nome": "Barolo Trattoria",
        "endereco": "R. Carlos de Carvalho, 271 - Centro, Curitiba - PR, 80410-170",
        "telefone": "(41) 3222-2096",
        "website": "www.barolotrattoria.com.br",
        "rating": "4.7",
        "total_reviews": "3215",
        "categoria": "Restaurante Italiano",
        "latitude": "-25.4321",
        "longitude": "-49.2762",
        "google_maps_url": "https://maps.google.com/?cid=barolo_trattoria",
        "tem_ifood": True,
        "ifood_nome": "Barolo Trattoria",
        "cnpj": "04.567.890/0001-12",
        "razao_social": "BAROLO RESTAURANTE LTDA",
        "nome_fantasia": "BAROLO TRATTORIA",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2001-06-15",
        "capital_social": 250000.00,
        "socios": [
            {"nome": "MARCO ANTONIO BERTOLI", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "LUCIA MARIA BERTOLI", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Cantina do Délio",
        "endereco": "R. Pres. Carlos Cavalcanti, 1222 - São Francisco, Curitiba - PR",
        "telefone": "(41) 3264-7843",
        "website": "www.cantinadodelio.com.br",
        "rating": "4.5",
        "total_reviews": "2890",
        "categoria": "Restaurante Italiano",
        "latitude": "-25.4198",
        "longitude": "-49.2634",
        "google_maps_url": "https://maps.google.com/?cid=cantina_delio",
        "tem_ifood": False,
        "ifood_nome": "",
        "cnpj": "76.345.210/0001-55",
        "razao_social": "DELIO RESTAURANTE E EVENTOS LTDA",
        "nome_fantasia": "CANTINA DO DELIO",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "1992-03-20",
        "capital_social": 180000.00,
        "socios": [
            {"nome": "DELIO CANEPPELE", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "MARIA APARECIDA CANEPPELE", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Sal Gastronomia",
        "endereco": "R. Coronel Dulcídio, 599 - Batel, Curitiba - PR, 80420-170",
        "telefone": "(41) 3044-8468",
        "website": "www.salgastronomia.com.br",
        "rating": "4.4",
        "total_reviews": "1567",
        "categoria": "Restaurante Contemporâneo",
        "latitude": "-25.4412",
        "longitude": "-49.2856",
        "google_maps_url": "https://maps.google.com/?cid=sal_gastronomia",
        "tem_ifood": False,
        "ifood_nome": "",
        "cnpj": "12.890.456/0001-33",
        "razao_social": "SAL GASTRONOMIA EIRELI",
        "nome_fantasia": "SAL GASTRONOMIA",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2015-08-10",
        "capital_social": 120000.00,
        "socios": [
            {"nome": "EDUARDO SGANZERLA", "qualificacao": "Titular", "tipo": "PF"},
        ],
    },
    {
        "nome": "Quintana Gastronomia",
        "endereco": "Al. Dom Pedro II, 317 - Batel, Curitiba - PR, 80420-060",
        "telefone": "(41) 3232-0049",
        "website": "www.quintana.com.br",
        "rating": "4.3",
        "total_reviews": "945",
        "categoria": "Restaurante Brasileiro",
        "latitude": "-25.4389",
        "longitude": "-49.2901",
        "google_maps_url": "https://maps.google.com/?cid=quintana",
        "tem_ifood": True,
        "ifood_nome": "Quintana Gastronomia",
        "cnpj": "08.765.432/0001-90",
        "razao_social": "QUINTANA RESTAURANTE LTDA",
        "nome_fantasia": "QUINTANA",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2010-02-28",
        "capital_social": 200000.00,
        "socios": [
            {"nome": "FELIPE KRAUSE DORNELLES", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "ANA PAULA KRAUSE", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Terrazza 40",
        "endereco": "R. Voluntários da Pátria, 540 - Centro, Curitiba - PR",
        "telefone": "(41) 3233-1540",
        "website": "www.terrazza40.com.br",
        "rating": "4.2",
        "total_reviews": "2100",
        "categoria": "Restaurante Italiano",
        "latitude": "-25.4290",
        "longitude": "-49.2712",
        "google_maps_url": "https://maps.google.com/?cid=terrazza40",
        "tem_ifood": True,
        "ifood_nome": "Terrazza 40",
        "cnpj": "05.432.109/0001-67",
        "razao_social": "TERRAZZA RESTAURANTE LTDA",
        "nome_fantasia": "TERRAZZA 40",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2005-11-12",
        "capital_social": 300000.00,
        "socios": [
            {"nome": "RICARDO LUIZ MACHADO", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
        ],
    },
    {
        "nome": "Restaurante Família Madalosso",
        "endereco": "Av. Manoel Ribas, 5875 - Santa Felicidade, Curitiba - PR",
        "telefone": "(41) 3372-2121",
        "website": "www.madalosso.com.br",
        "rating": "4.1",
        "total_reviews": "15780",
        "categoria": "Restaurante Italiano",
        "latitude": "-25.3912",
        "longitude": "-49.3345",
        "google_maps_url": "https://maps.google.com/?cid=madalosso",
        "tem_ifood": False,
        "ifood_nome": "",
        "cnpj": "76.098.765/0001-44",
        "razao_social": "MADALOSSO RESTAURANTE E TURISMO LTDA",
        "nome_fantasia": "RESTAURANTE MADALOSSO",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "1963-07-01",
        "capital_social": 5000000.00,
        "socios": [
            {"nome": "GIOVANI MADALOSSO", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "MADALOSSO PARTICIPACOES LTDA", "qualificacao": "Sócio", "tipo": "PJ"},
            {"nome": "ANA MARIA MADALOSSO", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Bouquet Garni Bistrô",
        "endereco": "R. Bispo Dom José, 2208 - Batel, Curitiba - PR",
        "telefone": "(41) 3242-8853",
        "website": "www.bouquetgarni.com.br",
        "rating": "4.8",
        "total_reviews": "678",
        "categoria": "Bistrô Francês",
        "latitude": "-25.4445",
        "longitude": "-49.2910",
        "google_maps_url": "https://maps.google.com/?cid=bouquet_garni",
        "tem_ifood": False,
        "ifood_nome": "",
        "cnpj": "15.678.901/0001-23",
        "razao_social": "BOUQUET GARNI RESTAURANTE EIRELI",
        "nome_fantasia": "BOUQUET GARNI",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2017-04-15",
        "capital_social": 90000.00,
        "socios": [
            {"nome": "JEAN PIERRE LAMBERT", "qualificacao": "Titular", "tipo": "PF"},
        ],
    },
    {
        "nome": "Dom Antônio Pizzaria",
        "endereco": "R. Comendador Araújo, 380 - Centro, Curitiba - PR",
        "telefone": "(41) 3224-5656",
        "website": "",
        "rating": "4.0",
        "total_reviews": "3456",
        "categoria": "Pizzaria",
        "latitude": "-25.4334",
        "longitude": "-49.2789",
        "google_maps_url": "https://maps.google.com/?cid=dom_antonio",
        "tem_ifood": True,
        "ifood_nome": "Dom Antônio Pizza",
        "cnpj": "03.210.987/0001-11",
        "razao_social": "DOM ANTONIO PIZZARIA LTDA",
        "nome_fantasia": "DOM ANTONIO",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "1998-01-10",
        "capital_social": 150000.00,
        "socios": [
            {"nome": "ANTONIO CARLOS PEREIRA", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "ROSA MARIA PEREIRA", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Kawiarnia Café",
        "endereco": "R. Trajano Reis, 152 - São Francisco, Curitiba - PR",
        "telefone": "(41) 3077-0029",
        "website": "www.kawiarnia.com.br",
        "rating": "4.6",
        "total_reviews": "412",
        "categoria": "Cafeteria",
        "latitude": "-25.4210",
        "longitude": "-49.2650",
        "google_maps_url": "https://maps.google.com/?cid=kawiarnia",
        "tem_ifood": True,
        "ifood_nome": "Kawiarnia",
        "cnpj": "20.456.789/0001-88",
        "razao_social": "KAWIARNIA CAFETERIA LTDA",
        "nome_fantasia": "KAWIARNIA",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2019-09-01",
        "capital_social": 60000.00,
        "socios": [
            {"nome": "PATRYCJA KOWALSKI", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "TOMASZ KOWALSKI", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Anarco Steak Bar",
        "endereco": "R. Kellers, 95 - Centro Cívico, Curitiba - PR",
        "telefone": "(41) 3352-9898",
        "website": "www.anarcosteakbar.com.br",
        "rating": "4.5",
        "total_reviews": "1890",
        "categoria": "Churrascaria",
        "latitude": "-25.4178",
        "longitude": "-49.2701",
        "google_maps_url": "https://maps.google.com/?cid=anarco",
        "tem_ifood": True,
        "ifood_nome": "Anarco Steak Bar",
        "cnpj": "18.345.678/0001-56",
        "razao_social": "ANARCO RESTAURANTE LTDA",
        "nome_fantasia": "ANARCO STEAK BAR",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2016-05-20",
        "capital_social": 200000.00,
        "socios": [
            {"nome": "RODRIGO FARIAS OLIVEIRA", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
            {"nome": "BRUNO FARIAS OLIVEIRA", "qualificacao": "Sócio", "tipo": "PF"},
        ],
    },
    {
        "nome": "Green Kitchen Curitiba",
        "endereco": "R. Desembargador Westphalen, 15 - Centro, Curitiba - PR",
        "telefone": "(41) 3045-1234",
        "website": "www.greenkitchen.com.br",
        "rating": "4.3",
        "total_reviews": "567",
        "categoria": "Restaurante Vegetariano",
        "latitude": "-25.4356",
        "longitude": "-49.2734",
        "google_maps_url": "https://maps.google.com/?cid=green_kitchen",
        "tem_ifood": False,
        "ifood_nome": "",
        "cnpj": "25.789.012/0001-77",
        "razao_social": "GREEN KITCHEN ALIMENTACAO SAUDAVEL LTDA",
        "nome_fantasia": "GREEN KITCHEN",
        "situacao_cadastral": "ATIVA",
        "data_abertura": "2020-01-15",
        "capital_social": 80000.00,
        "socios": [
            {"nome": "CAROLINA MENDES SILVA", "qualificacao": "Sócio-Administrador", "tipo": "PF"},
        ],
    },
]


# ============================================================
# FUNÇÕES DE SIMULAÇÃO (com delays visuais)
# ============================================================

def tipo(texto, delay=0.02):
    """Simula digitação no terminal."""
    for char in texto:
        print(char, end="", flush=True)
        time.sleep(delay)
    print()


def log(texto, delay=0.3):
    """Imprime log com delay."""
    print(texto)
    time.sleep(delay)


def barra_progresso(atual, total, prefixo="", tamanho=40):
    """Mostra barra de progresso."""
    pct = atual / total
    preenchido = int(tamanho * pct)
    barra = "█" * preenchido + "░" * (tamanho - preenchido)
    print(f"\r  {prefixo} [{barra}] {atual}/{total} ({pct*100:.0f}%)", end="", flush=True)
    if atual == total:
        print()


def separador(titulo=""):
    if titulo:
        print(f"\n{'='*64}")
        print(f"  {titulo}")
        print(f"{'='*64}")
    else:
        print(f"{'─'*64}")


# ============================================================
# SIMULAÇÃO PRINCIPAL
# ============================================================

def simular_banner():
    os.system("cls" if os.name == "nt" else "clear")
    print("""
╔══════════════════════════════════════════════════════════════╗
║            🍽️  RESTAURANT BI - Prospecção Inteligente       ║
║            Mapeamento Nacional de Restaurantes               ║
╠══════════════════════════════════════════════════════════════╣
║  Google Maps → iFood Check → CNPJ/Sócios → Excel/CSV       ║
╚══════════════════════════════════════════════════════════════╝
    """)


def simular_menu():
    print("""
┌──────────────────────────────────────────┐
│           MENU PRINCIPAL                 │
├──────────────────────────────────────────┤
│  [1] 🔍 Varredura Google Maps            │
│  [2] 🛵 Verificar iFood                  │
│  [3] 📋 Enriquecer CNPJ/Sócios           │
│  [4] 🔄 Pipeline Completo (1+2+3)        │
│  [5] 📊 Consultar Banco de Dados          │
│  [6] 📥 Exportar Excel                    │
│  [7] 📥 Exportar CSV                      │
│  [8] 📈 Estatísticas Gerais               │
│  [9] 🗑️  Resetar Banco                    │
│  [0] ❌ Sair                              │
└──────────────────────────────────────────┘
    """)
    print('Escolha uma opção: 4  ← [Pipeline Completo]')
    time.sleep(1)


def simular_selecao_cidade():
    print("""
┌── SELECIONAR CIDADE ──┐
│  [0] Todas as capitais │
│  [ 1] São Paulo/SP     │
│  [ 2] Rio de Janeiro/RJ│
│  [ 3] Belo Horizonte/MG│
│  [ 4] Curitiba/PR      │
│  [ 5] Porto Alegre/RS  │
│  ...                    │
└─────────────────────────┘
    """)
    print("Escolha (número): 4  ← [Curitiba/PR]")
    time.sleep(0.8)

    print("""
┌── MODO DE VARREDURA ──────────────────────────┐
│  [1] 🐢 Completo (mais dados, mais lento)      │
│      → Clica em cada restaurante               │
│      → Extrai telefone, website, coordenadas   │
│                                                 │
│  [2] 🐇 Rápido (dados básicos, muito rápido)  │
│      → Extrai da lista sem clicar              │
│      → Nome, endereço, rating, categoria       │
└─────────────────────────────────────────────────┘
    """)
    print("Escolha (1 ou 2): 1  ← [Modo Completo]")
    time.sleep(0.5)
    print("\nRodar invisível (headless)? [S/n]: S")
    time.sleep(0.5)


def simular_etapa1_gmaps():
    """Simula a Etapa 1: Varredura do Google Maps."""
    separador("PIPELINE COMPLETO: Curitiba/PR")
    time.sleep(0.5)

    print("\n── ETAPA 1/3: Google Maps ──")
    time.sleep(0.3)

    log('[LOG] 🔍 Iniciando varredura: "restaurantes em Curitiba PR"')
    log("[LOG] 🌐 URL: https://www.google.com.br/maps/search/restaurantes+em+Curitiba+PR")
    log("[LOG] 🚀 Acessando Google Maps...")
    time.sleep(1.5)
    log("[LOG] ✅ Modal de cookies aceito.")
    time.sleep(0.5)
    log("[LOG] 📜 Iniciando scroll na lista de resultados...")

    # Simular scrolls
    counts = [8, 17, 25, 34, 42, 51, 59, 68, 76, 85, 93, 102, 110, 119, 127,
              135, 144, 152, 160, 168, 176, 185, 193, 201, 210, 218, 226, 234,
              242, 251, 259, 267, 275, 284, 292, 300, 308, 317, 325, 333, 341,
              350, 358, 366, 374, 383, 391, 399, 407, 416, 424, 432, 440, 449,
              457, 465, 473, 481, 488, 490, 490, 490, 490, 490]

    for i, c in enumerate(counts):
        if c % 40 == 0 or i == len(counts) - 1:
            log(f"[LOG] 🔄 Scroll {i+1}: {c} restaurantes carregados...")
        time.sleep(0.08)

    log("[LOG] 📋 Final da lista atingido após 64 scrolls.")
    log(f"[LOG] ✅ Total de cards carregados: 490")
    time.sleep(0.5)
    log(f"[LOG] 🏪 Extraindo detalhes de 490 restaurantes...")
    print()

    # Simular extração detalhada dos 12 restaurantes de exemplo
    for i, rest in enumerate(RESTAURANTES_SIMULADOS):
        tel = f" | Tel: {rest['telefone']}" if rest['telefone'] else ""
        web = f" | Web: {rest['website']}" if rest['website'] else ""
        print(f"[+] ({i+1}/490) {rest['nome']}{tel}{web}")
        time.sleep(0.2)

        if (i + 1) % 10 == 0:
            log(f"[LOG] ⏳ Pausa de segurança... ({i+1}/490)")
            time.sleep(0.3)

    # Simular o restante
    print(f"[+] (13/490) Pasta di Casa Trattoria | Tel: (41) 3019-5678 | Web: www.pastadicasa.com.br")
    time.sleep(0.1)
    print(f"[+] (14/490) Churrascaria Jardins Grill | Tel: (41) 3345-2200")
    time.sleep(0.1)
    print(f"[+] (15/490) Sushi Naka Curitiba | Tel: (41) 3078-9090 | Web: www.sushinaka.com.br")
    time.sleep(0.1)
    print(f"  ...")
    print(f"  ... [extraindo mais 475 restaurantes] ...")
    print(f"  ...")
    time.sleep(0.5)
    print(f"[+] (488/490) Lanchonete do Zé | Tel: (41) 3299-0011")
    time.sleep(0.1)
    print(f"[+] (489/490) Padaria Confeitaria Paris | Tel: (41) 3256-7788 | Web: www.padariaparis.com")
    time.sleep(0.1)
    print(f"[+] (490/490) Taco Bell - Shopping Estação | Tel: (41) 3200-4400 | Web: www.tacobell.com.br")
    time.sleep(0.3)

    print(f"\n[SUCESSO] ✅ Curitiba/PR: 490 restaurantes extraídos com sucesso!")
    time.sleep(0.3)
    print(f"[DB] 💾 487 novos restaurantes salvos no banco (de 490 encontrados, 3 duplicatas ignoradas)")
    time.sleep(0.5)


def simular_etapa2_ifood():
    """Simula a Etapa 2: Verificação iFood."""
    print(f"\n── ETAPA 2/3: Verificação iFood ──")
    time.sleep(0.5)

    total = len(RESTAURANTES_SIMULADOS)

    for i, rest in enumerate(RESTAURANTES_SIMULADOS):
        status = "✅ SIM" if rest["tem_ifood"] else "❌ NÃO"
        ifood_extra = f" → {rest['ifood_nome']}" if rest["tem_ifood"] else ""
        print(f"[iFood] ({i+1}/490) {rest['nome']}: {status}{ifood_extra}")
        time.sleep(0.15)

        if (i + 1) % 5 == 0:
            log(f"[iFood] ⏳ Pausa de segurança... ({i+1}/490)")
            time.sleep(0.2)

    print(f"  ...")
    print(f"  ... [verificando mais 478 restaurantes] ...")
    print(f"  ...")
    time.sleep(0.4)
    print(f"[iFood] (489/490) Padaria Confeitaria Paris: ❌ NÃO")
    print(f"[iFood] (490/490) Taco Bell - Shopping Estação: ✅ SIM → Taco Bell")
    time.sleep(0.3)

    com_ifood = 7  # dos 12 simulados
    total_ifood = 312  # simulado
    print(f"\n[iFood] ✅ Verificação concluída: {total_ifood}/490 no iFood")
    time.sleep(0.5)


def simular_etapa3_cnpj():
    """Simula a Etapa 3: Enriquecimento CNPJ/Sócios."""
    print(f"\n── ETAPA 3/3: Enriquecimento CNPJ ──")
    time.sleep(0.5)

    for i, rest in enumerate(RESTAURANTES_SIMULADOS):
        print(f"\n[CNPJ] === Processando {i+1}/490 ===")
        log(f"[CNPJ] 🔍 Buscando CNPJ: {rest['nome']} (Curitiba)...")
        time.sleep(0.3)
        log(f"[CNPJ] 📋 CNPJ encontrado: {rest['cnpj']}")
        time.sleep(0.2)

        socios_pf = [s for s in rest["socios"] if s["tipo"] == "PF"]
        socios_nomes = ", ".join([s["nome"] for s in socios_pf])
        log(f"[SUCESSO] ✅ {rest['nome']}: {rest['razao_social']} | Sócios PF: {socios_nomes}")
        time.sleep(0.15)

        log(f"[CNPJ] ⏳ Aguardando {random.uniform(5, 12):.1f}s...")
        time.sleep(0.1)

    print(f"\n  ...")
    print(f"  ... [enriquecendo mais 478 restaurantes] ...")
    print(f"  ...")
    time.sleep(0.5)
    print(f"\n[CNPJ] ✅ Enriquecimento concluído: 423/490 com dados societários")
    time.sleep(0.5)


def simular_exportacao():
    """Simula a exportação do Excel."""
    print(f"\n── EXPORTAÇÃO AUTOMÁTICA ──")
    time.sleep(0.3)
    log("[EXPORT] ✅ Excel exportado: ./exports/restaurantes_Curitiba_PR_20260211_1430.xlsx")
    log("[EXPORT] 📊 Total de registros: 490")
    time.sleep(0.5)


def simular_estatisticas():
    """Simula a tela de estatísticas finais."""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    📈 ESTATÍSTICAS GERAIS                   ║
╠══════════════════════════════════════════════════════════════╣
║  Total de restaurantes mapeados:     490                    ║
║  ├── Pendentes:                        0                    ║
║  ├── Processados (Maps):               0                    ║
║  ├── iFood verificado:                67                    ║
║  └── Enriquecidos (CNPJ):            423                    ║
║                                                              ║
║  🛵 Com presença no iFood:           312                    ║
║  📋 Com CNPJ identificado:           423                    ║
║  👤 Total de sócios mapeados:        847                    ║
╚══════════════════════════════════════════════════════════════╝
    """)
    time.sleep(1)


def simular_consulta_banco():
    """Simula a consulta ao banco de dados."""
    separador("CONSULTA: Curitiba/PR — 490 restaurantes")
    print()

    # Header
    print(f"  {'#':>3}  {'':2} {'Nome':<36} {'Telefone':<17} {'Rating':>6}")
    print(f"  {'─'*68}")

    for i, rest in enumerate(RESTAURANTES_SIMULADOS, 1):
        ifood = "🛵" if rest["tem_ifood"] else "  "
        cnpj = "📋" if rest["cnpj"] else "  "
        tel = rest["telefone"] if rest["telefone"] else "S/ tel"
        nome = rest["nome"][:35]
        print(f"  {i:3d}. {ifood}{cnpj} {nome:<35} {tel:<17} ⭐{rest['rating']}")
        time.sleep(0.08)

    print(f"  13.   📋 Pasta di Casa Trattoria            (41) 3019-5678    ⭐4.4")
    print(f"  14. 🛵📋 Churrascaria Jardins Grill          (41) 3345-2200    ⭐4.2")
    print(f"  15. 🛵📋 Sushi Naka Curitiba                 (41) 3078-9090    ⭐4.6")
    print(f"  ...")
    print(f"  ... e mais 475 restaurantes. Exporte para ver todos.")
    time.sleep(0.5)

    print(f"\n  Legenda: 🛵 = tem iFood  |  📋 = CNPJ encontrado")


def simular_preview_excel():
    """Simula como ficaria o Excel exportado."""
    separador("PREVIEW DO EXCEL EXPORTADO")
    print()
    print("  📁 Arquivo: ./exports/restaurantes_Curitiba_PR_20260211_1430.xlsx")
    print()
    print("  📄 Aba 'Restaurantes' (490 linhas):")
    print(f"  {'─'*120}")
    print(f"  {'Nome':<28} {'Endereço':<35} {'Telefone':<17} {'Website':<25} {'iFood':>5} {'CNPJ':<20} {'Sócios'}")
    print(f"  {'─'*120}")

    for rest in RESTAURANTES_SIMULADOS[:6]:
        nome = rest["nome"][:27]
        end = rest["endereco"][:34]
        tel = rest["telefone"]
        web = rest["website"][:24] if rest["website"] else ""
        ifood = "SIM" if rest["tem_ifood"] else "NÃO"
        cnpj = rest["cnpj"]
        socios_pf = [s["nome"] for s in rest["socios"] if s["tipo"] == "PF"]
        socios = socios_pf[0][:20] if socios_pf else ""
        print(f"  {nome:<28} {end:<35} {tel:<17} {web:<25} {ifood:>5} {cnpj:<20} {socios}")
        time.sleep(0.1)

    print(f"  ... (mais 484 linhas)")
    print()
    
    print("  📄 Aba 'Resumo por Cidade' (1 linha):")
    print(f"  {'─'*80}")
    print(f"  {'Cidade':<15} {'UF':<4} {'Total':>6} {'Com iFood':>10} {'Sem iFood':>10} {'Com CNPJ':>10} {'Com Tel':>8}")
    print(f"  {'─'*80}")
    print(f"  {'Curitiba':<15} {'PR':<4} {'490':>6} {'312':>10} {'178':>10} {'423':>10} {'461':>8}")
    print()

    print("  📄 Aba 'Com iFood' (312 linhas):")
    print(f"  → Restaurantes que JÁ usam delivery digital")
    print()
    print("  📄 Aba 'Sem iFood (Oportunidade)' (178 linhas):  ← 🔥 OURO PARA PROSPECÇÃO")
    print(f"  → Restaurantes que NÃO usam delivery → candidatos perfeitos pro Super Food!")
    print()

    # Mostrar exemplos da aba "oportunidade"
    print("  Preview da aba 'Sem iFood (Oportunidade)':")
    print(f"  {'─'*100}")
    sem_ifood = [r for r in RESTAURANTES_SIMULADOS if not r["tem_ifood"]]
    for rest in sem_ifood:
        socios_pf = [s["nome"] for s in rest["socios"] if s["tipo"] == "PF"]
        dono = socios_pf[0] if socios_pf else "N/A"
        print(f"  📞 {rest['nome']:<35} {rest['telefone']:<17} Dono: {dono}")
    print(f"  📞 {'Sabor Natural Express':<35} {'(41) 3088-5566':<17} Dono: FERNANDA COSTA RIBEIRO")
    print(f"  📞 {'Poke Hana Curitiba':<35} {'(41) 3041-7890':<17} Dono: LUCAS TANAKA")
    print(f"  ... (mais 171 restaurantes sem iFood)")
    time.sleep(0.5)


def simular_abordagem_comercial():
    """Mostra como usar os dados na prática para prospecção."""
    separador("💡 COMO USAR ESSES DADOS PARA PROSPECÇÃO")
    print("""
  Com os dados em mãos, você pode criar abordagens super personalizadas:

  ┌─────────────────────────────────────────────────────────────────────┐
  │  EXEMPLO DE ABORDAGEM VIA WHATSAPP:                                │
  │                                                                     │
  │  "Oi, Délio! Tudo bem?                                             │
  │   Sou o Klenilton, vi que a Cantina do Délio no São Francisco      │
  │   tem nota 4.5 no Google com quase 3 mil avaliações! 🔥            │
  │   Notei que vocês ainda não estão no iFood.                        │
  │   Temos uma plataforma de delivery que cobra MUITO menos           │
  │   comissão. Posso te mostrar em 5 min?"                            │
  └─────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────┐
  │  EXEMPLO DE ABORDAGEM VIA EMAIL:                                   │
  │                                                                     │
  │  Para: contato@bouquetgarni.com.br                                 │
  │  Assunto: Delivery premium para o Bouquet Garni 🍽️                │
  │                                                                     │
  │  "Jean Pierre, boa tarde!                                          │
  │   Sabemos que bistrôs de alta gastronomia como o Bouquet Garni     │
  │   precisam de um delivery que preserve a experiência...            │
  │   O Super Food foi feito para restaurantes como o seu..."          │
  └─────────────────────────────────────────────────────────────────────┘

  DADOS QUE VOCÊ TEM PARA CADA RESTAURANTE:
  ✅ Nome do dono/sócios (para abordagem pessoal)
  ✅ Telefone direto (para WhatsApp)
  ✅ Website (para encontrar email)
  ✅ Se tem iFood ou não (para argumentação)
  ✅ Rating e reviews (para elogio genuíno)
  ✅ CNPJ e razão social (para proposta formal)
  ✅ Endereço (para visita presencial)
  ✅ Capital social (para segmentar por porte)
    """)


# ============================================================
# EXECUÇÃO DA SIMULAÇÃO
# ============================================================

def main():
    simular_banner()
    time.sleep(1)

    print("[DB] ✅ Banco de dados inicializado com sucesso.")
    time.sleep(0.5)

    simular_menu()
    time.sleep(0.5)
    simular_selecao_cidade()
    time.sleep(0.5)

    # Etapa 1: Google Maps
    simular_etapa1_gmaps()
    time.sleep(0.5)

    # Etapa 2: iFood
    simular_etapa2_ifood()
    time.sleep(0.5)

    # Etapa 3: CNPJ
    simular_etapa3_cnpj()
    time.sleep(0.5)

    # Exportação
    simular_exportacao()
    time.sleep(0.5)

    # Estatísticas
    print("\n[PIPELINE] ✅ Curitiba/PR concluído!")
    time.sleep(0.5)
    simular_estatisticas()
    time.sleep(0.5)

    # Consulta ao banco
    simular_consulta_banco()
    time.sleep(0.5)

    # Preview Excel
    print()
    simular_preview_excel()
    time.sleep(0.5)

    # Uso prático
    simular_abordagem_comercial()

    print(f"\n{'='*64}")
    print(f"  FIM DA SIMULAÇÃO")
    print(f"  Para rodar o sistema REAL: python main.py")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    main()
