"""
main.py - Orquestrador Principal do Restaurant BI v2.0
Pipeline: Receita Federal → Google Maps → iFood → Cruzamento por Endereço

FLUXO:
  [A] Coletar CNPJs da Receita (Casa dos Dados) → cnpjs_receita
  [B] Detalhar CNPJs (OpenCNPJ) → sócios, email
  [1] Varredura Google Maps → restaurantes
  [2] Verificar iFood
  [C] Cruzar Endereços (Receita × Maps) → match CNPJ
  [P] Pipeline Completo (A+B+1+2+C)
"""
import asyncio
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import CAPITAIS, STATUS_PENDENTE, STATUS_PROCESSADO, STATUS_IFOOD_CHECKED
from init_db import init_database
from db_manager import (
    inserir_restaurantes_batch, buscar_pendentes, buscar_por_cidade,
    atualizar_status, atualizar_ifood, atualizar_cnpj, inserir_socios,
    estatisticas_gerais, registrar_varredura, finalizar_varredura,
    cidades_ja_varridas, resetar_banco,
)
from gmaps_scraper import scrape_restaurantes_cidade, scrape_cidade_simples
from ifood_checker import verificar_ifood_batch
from exporter import exportar_excel, exportar_csv
from receita_fetcher import (
    coletar_cnpjs_cidade, detalhar_cnpjs_cidade,
    estatisticas_receita, init_tabela_receita,
)
from address_matcher import cruzar_cidade_completa, testar_similaridade


def limpar_tela():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║          🍽️  RESTAURANT BI v2.0 - Prospecção Inteligente        ║
║     Receita Federal × Google Maps × iFood × Cruzamento         ║
╠══════════════════════════════════════════════════════════════════╣
║  Base Receita (CNPJ+endereço) → Google Maps → iFood → Match    ║
╚══════════════════════════════════════════════════════════════════╝
    """)


def menu_principal():
    print("""
┌──────────────────────────────────────────────────────┐
│              MENU PRINCIPAL v2.0                      │
├──────────────────────────────────────────────────────┤
│  ── RECEITA FEDERAL ──                               │
│  [A] 🏛️  Coletar CNPJs (Casa dos Dados)              │
│  [B] 📋 Detalhar CNPJs (sócios/email OpenCNPJ)      │
│                                                      │
│  ── GOOGLE MAPS + iFOOD ──                           │
│  [1] 🔍 Varredura Google Maps                        │
│  [2] 🛵 Verificar iFood                              │
│                                                      │
│  ── CRUZAMENTO ──                                    │
│  [C] 🔗 Cruzar Endereços (Receita × Maps)           │
│                                                      │
│  ── AUTOMÁTICO ──                                    │
│  [P] 🚀 Pipeline Completo (A+B+1+2+C)               │
│                                                      │
│  ── CONSULTAS ──                                     │
│  [5] 📊 Consultar Banco    [6] 📥 Exportar Excel    │
│  [7] 📥 Exportar CSV       [8] 📈 Estatísticas      │
│  [T] 🧪 Testar Algoritmo   [9] 🗑️  Resetar          │
│  [0] ❌ Sair                                         │
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
        qtd = stats_receita.get((cap["cidade"].upper(), cap["uf"]), 0)
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

async def acao_coletar_receita():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        print(f"\n{'='*60}")
        print(f"  🏛️ COLETA RECEITA: {c}/{u} (incremental)")
        print(f"{'='*60}")
        stats = await coletar_cnpjs_cidade(c, u)
        if stats["novos"] > 0:
            print(f"\n✅ {stats['novos']} CNPJs novos! Base total: {stats['base_total']}")
        else:
            print(f"\n✅ Base atualizada. Nenhum CNPJ novo.")
    input("\n[ENTER para continuar...]")


async def acao_detalhar_cnpjs():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    limite_input = input("\nQuantos CNPJs detalhar? [100]: ").strip()
    limite = int(limite_input) if limite_input.isdigit() else 100
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        print(f"\n{'='*60}")
        print(f"  📋 DETALHAMENTO: {c}/{u} (incremental)")
        print(f"{'='*60}")
        await detalhar_cnpjs_cidade(c, u, limite)
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
        print(f"\n{'='*60}")
        print(f"  🔍 GOOGLE MAPS: {c}/{u}")
        print(f"{'='*60}")
        registrar_varredura(c, u)
        if modo == "2":
            resultados = await scrape_cidade_simples(c, u, headless)
        else:
            resultados = await scrape_restaurantes_cidade(c, u, headless)
        if resultados:
            inseridos = inserir_restaurantes_batch(resultados)
            print(f"[DB] 💾 {inseridos} novos (de {len(resultados)} encontrados)")
            restaurantes_db = buscar_por_cidade(c, u)
            for r in restaurantes_db:
                if r["status"] == STATUS_PENDENTE:
                    atualizar_status(r["id"], STATUS_PROCESSADO)
            finalizar_varredura(c, u, len(resultados))
        else:
            print(f"[WARN] Nenhum restaurante em {c}/{u}")
    input("\n[ENTER para continuar...]")


async def acao_verificar_ifood():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    pendentes = buscar_pendentes(STATUS_PROCESSADO, cidade, uf, limite=500) if cidade \
        else buscar_pendentes(STATUS_PROCESSADO, limite=500)
    if not pendentes:
        print("[INFO] Nenhum pendente de iFood.")
        input("\n[ENTER para continuar...]")
        return
    print(f"\n[iFood] 🛵 {len(pendentes)} para verificar.")
    if input("Continuar? [S/n]: ").strip().lower() == "n":
        return
    headless = input("Headless? [S/n]: ").strip().lower() != "n"
    resultados = await verificar_ifood_batch(pendentes, headless)
    for res in resultados:
        atualizar_ifood(res["id"], res["tem_ifood"], res["ifood_nome"], res["ifood_url"])
    com = sum(1 for r in resultados if r["tem_ifood"])
    print(f"\n[iFood] ✅ {com}/{len(resultados)} no iFood")
    input("\n[ENTER para continuar...]")


async def acao_cruzar_enderecos():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        print(f"\n{'='*60}")
        print(f"  🔗 CRUZAMENTO: {c}/{u}")
        print(f"  (Só restaurantes sem CNPJ)")
        print(f"{'='*60}")
        cruzar_cidade_completa(c, u)
    input("\n[ENTER para continuar...]")


async def acao_pipeline_completo():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return

    print(f"""
╔══════════════════════════════════════════════════════════╗
║              🚀 PIPELINE COMPLETO v2.0                  ║
║  A: Receita → B: Sócios → 1: Maps → 2: iFood → C: Match║
╚══════════════════════════════════════════════════════════╝""")

    modo = selecionar_modo_scraping()
    headless = input("Headless? [S/n]: ").strip().lower() != "n"
    if input("\n🚀 Iniciar? [S/n]: ").strip().lower() == "n":
        return

    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]

    for c, u in cidades:
        print(f"\n{'='*70}")
        print(f"  PIPELINE: {c}/{u}")
        print(f"{'='*70}")

        # A: Receita
        print(f"\n── A/5: Coleta Receita Federal ──")
        try:
            st = await coletar_cnpjs_cidade(c, u)
            print(f"[RECEITA] ✅ {st['novos']} novos | Base: {st['base_total']}")
        except Exception as e:
            print(f"[RECEITA] ⚠️ {e}")

        # B: Detalhar
        print(f"\n── B/5: Detalhamento (sócios/email) ──")
        try:
            st = await detalhar_cnpjs_cidade(c, u, 500)
            print(f"[DETALHE] ✅ {st['detalhados']} detalhados")
        except Exception as e:
            print(f"[DETALHE] ⚠️ {e}")

        # 1: Maps
        print(f"\n── 1/5: Google Maps ──")
        registrar_varredura(c, u)
        if modo == "2":
            res = await scrape_cidade_simples(c, u, headless)
        else:
            res = await scrape_restaurantes_cidade(c, u, headless)
        if res:
            ins = inserir_restaurantes_batch(res)
            print(f"[MAPS] 💾 {ins} novos salvos")
            finalizar_varredura(c, u, len(res))
            for r in buscar_por_cidade(c, u):
                if r["status"] == STATUS_PENDENTE:
                    atualizar_status(r["id"], STATUS_PROCESSADO)

        # 2: iFood
        print(f"\n── 2/5: iFood ──")
        pend = buscar_pendentes(STATUS_PROCESSADO, c, u, limite=500)
        if pend:
            res_if = await verificar_ifood_batch(pend, headless)
            for r in res_if:
                atualizar_ifood(r["id"], r["tem_ifood"], r["ifood_nome"], r["ifood_url"])
            com = sum(1 for r in res_if if r["tem_ifood"])
            print(f"[IFOOD] ✅ {com}/{len(res_if)} no iFood")

        # C: Cruzamento
        print(f"\n── C/5: Cruzamento Endereços ──")
        st_m = cruzar_cidade_completa(c, u)
        print(f"\n[PIPELINE] ✅ {c}/{u} PRONTO! Match: {st_m.get('matched', 0)} ({st_m.get('taxa', 'N/A')})")

    # Exportar
    print(f"\n── EXPORTAÇÃO ──")
    exportar_excel(cidade, uf) if cidade else exportar_excel()
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
              f"Matched: {stats_rec['matched']}")
        if stats['por_cidade']:
            print(f"\n  {'Cidade':<20} {'Maps':<7} {'iFood':<7} {'CNPJ':<7}")
            print(f"  {'─'*44}")
            for c in stats['por_cidade']:
                print(f"  {c['cidade']:<20} {c['total']:<7} {c['com_ifood']:<7} {c['com_cnpj']:<7}")
        input("\n[ENTER para continuar...]")
        return

    dados = buscar_por_cidade(cidade, uf)
    if not dados:
        print(f"[INFO] Nenhum restaurante em {cidade}/{uf}.")
        input("\n[ENTER...]")
        return

    st = estatisticas_receita(cidade.upper(), uf)
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
║               📈 ESTATÍSTICAS v2.0                    ║
╠════════════════════════════════════════════════════════╣
║  ── Google Maps ──                                    ║
║  Restaurantes: {stats['total']:>6}  Pendentes: {stats['pendentes']:>6}           ║
║  iFood: {stats['com_ifood']:>6}        CNPJ: {stats['com_cnpj']:>6}              ║
║  Sócios: {stats['total_socios']:>6}                                      ║
║                                                       ║
║  ── Receita Federal ──                                ║
║  CNPJs base: {stats_rec['total']:>6}   Detalhados: {stats_rec['detalhados']:>6}        ║
║  Matched: {stats_rec['matched']:>6}      Email: {stats_rec['com_email']:>6}             ║
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
            if op == "A": await acao_coletar_receita()
            elif op == "B": await acao_detalhar_cnpjs()
            elif op == "1": await acao_varredura()
            elif op == "2": await acao_verificar_ifood()
            elif op == "C": await acao_cruzar_enderecos()
            elif op == "P": await acao_pipeline_completo()
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
            print("\n[INFO] Interrompido. Dados salvos.")
            input("[ENTER para voltar ao menu...]")
        except Exception as e:
            print(f"\n[ERRO] {e}")
            import traceback
            traceback.print_exc()
            input("[ENTER...]")


if __name__ == "__main__":
    asyncio.run(main())
