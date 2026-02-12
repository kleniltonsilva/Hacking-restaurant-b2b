# 🍽️ Restaurant BI v2.0 - Prospecção Inteligente

Sistema de mapeamento de restaurantes com cruzamento Receita Federal × Google Maps.

## Arquitetura v2.0

```
Casa dos Dados (CNAE+cidade) → cnpjs_receita (endereço Receita Federal)
                                         ↓
OpenCNPJ (detalhamento)      → sócios, email, capital social
                                         ↓
Google Maps (scraping)       → restaurantes (endereço do Maps)
                                         ↓
              ADDRESS MATCHER: cruza endereço Receita × Maps
                                         ↓
                              CNPJ confirmado por endereço (~95%)
```

## Módulos

| Arquivo | Função |
|---------|--------|
| `receita_fetcher.py` | Coleta CNPJs por CNAE+cidade (Casa dos Dados) + detalha (OpenCNPJ) |
| `address_matcher.py` | Cruza endereços Receita × Maps (normalização inteligente) |
| `gmaps_scraper.py` | Scraping stealth do Google Maps (Playwright) |
| `ifood_checker.py` | Verifica presença no iFood |
| `cnpj_enricher.py` | Enriquecimento legacy (busca por nome - fallback) |
| `db_manager.py` | Operações no SQLite |
| `exporter.py` | Exportação Excel/CSV profissional |
| `main.py` | Orquestrador com menu interativo |

## Pipeline

```
[A] Coletar CNPJs da Receita (Casa dos Dados - incremental)
[B] Detalhar CNPJs (sócios/email via OpenCNPJ - incremental)
[1] Varredura Google Maps
[2] Verificar iFood
[C] Cruzar Endereços (match por endereço)
[P] Pipeline Completo Automático (A+B+1+2+C)
```

## Incremental

- **Receita**: só baixa CNPJs novos (não re-baixa existentes)
- **Maps**: só insere restaurantes novos (unique nome+cidade)
- **Detalhamento**: só detalha CNPJs pendentes
- **Cruzamento**: só cruza restaurantes sem CNPJ

## Como rodar

```bash
pip install -r requirements.txt
playwright install chromium
python main.py
```
