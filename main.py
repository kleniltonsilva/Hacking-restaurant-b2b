"""
main.py - Orquestrador Principal do Restaurant BI v3.0
Pipeline: Receita → cnpj.biz (tel proprietário) → Maps → Cruzamento → iFood

FLUXO:
  [A] Coletar CNPJs da Receita (Casa dos Dados) → cnpjs_receita
  [B] Detalhar CNPJs (cnpj.biz + tel proprietário)
  [C] Varredura Google Maps
  [D] Cruzar Endereços (cnpj.biz × Maps)
  [E] Verificar iFood (com nome confirmado do Maps ou Receita)
  [P] Pipeline Completo (A+B+C+D+E)
"""
import asyncio
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import CAPITAIS, STATUS_PENDENTE, STATUS_PROCESSADO, STATUS_IFOOD_CHECKED
from init_db import init_database
from db_manager import (
    inserir_restaurante, inserir_restaurantes_batch, buscar_pendentes,
    buscar_por_cidade, atualizar_status, atualizar_ifood, atualizar_cnpj,
    inserir_socios, estatisticas_gerais, registrar_varredura,
    finalizar_varredura, cidades_ja_varridas, resetar_banco,
    nomes_restaurantes_cidade, inserir_restaurante_e_vincular,
)
from gmaps_scraper import scrape_restaurantes_cidade, scrape_cidade_simples, scrape_maps_direcionado
from ifood_checker import verificar_ifood_batch
from exporter import exportar_excel, exportar_csv
from receita_fetcher import (
    coletar_cnpjs_cidade, detalhar_cnpjs_cidade,
    estatisticas_receita, init_tabela_receita,
    obter_cnpjs_sem_ifood, atualizar_ifood_receita,
)
from address_matcher import cruzar_cidade_completa, detectar_multi_restaurante, testar_similaridade
from logger import log


def limpar_tela():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║          🍽️  RESTAURANT BI v3.0 - Prospecção Inteligente        ║
║   Receita × cnpj.biz (Tel Proprietário) × iFood × Maps         ║
╠══════════════════════════════════════════════════════════════════╣
║  Receita → cnpj.biz → Maps → Match → iFood | TEL PROPRIETÁRIO  ║
╚══════════════════════════════════════════════════════════════════╝
    """)


def menu_principal():
    print("""
┌──────────────────────────────────────────────────────┐
│              MENU PRINCIPAL v3.0                      │
├──────────────────────────────────────────────────────┤
│  ── PIPELINE ──                                      │
│  [A] 🏛️  Coletar CNPJs (Casa dos Dados)              │
│  [B] 📱 Detalhar CNPJs (cnpj.biz + Tel Proprietário)│
│  [C] 🔍 Varredura Maps (genérica - por cidade)        │
│  [F] 🎯 Busca Maps Direcionada (por endereço CNPJ)  │
│  [D] 🔗 Cruzar Endereços (cnpj.biz × Maps)          │
│  [E] 🛵 Verificar iFood (nome confirmado)           │
│                                                      │
│  ── AUTOMÁTICO ──                                    │
│  [P] 🚀 Pipeline Completo (A+B+C+D+E)               │
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
        log.info(f"{'='*60}")
        log.info(f"  🏛️ COLETA RECEITA: {c}/{u} (incremental)")
        log.info(f"{'='*60}")
        stats = await coletar_cnpjs_cidade(c, u)
        if stats["novos"] > 0:
            log.info(f"✅ {stats['novos']} CNPJs novos! Base total: {stats['base_total']}")
        else:
            log.info(f"✅ Base atualizada. Nenhum CNPJ novo.")
    input("\n[ENTER para continuar...]")


async def acao_detalhar_cnpjs():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    limite_input = input("\nQuantos CNPJs detalhar? [0=todos]: ").strip()
    limite = int(limite_input) if limite_input.isdigit() else 0
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        log.info(f"{'='*60}")
        log.info(f"  📋 DETALHAMENTO: {c}/{u} (incremental)")
        log.info(f"{'='*60}")
        await detalhar_cnpjs_cidade(c, u, limite)
    input("\n[ENTER para continuar...]")


async def acao_verificar_ifood():
    """Verifica iFood usando nome confirmado (Maps ou nome_fantasia da Receita).
    MEIs sem nome fantasia são pulados."""
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]

    for c, u in cidades:
        cnpjs_pendentes = obter_cnpjs_sem_ifood(c, u)
        if not cnpjs_pendentes:
            log.info(f"[iFood] ✅ Todos CNPJs de {c}/{u} já verificados no iFood.")
            continue

        # Escolher melhor nome: Maps > nome_fantasia > pular
        items = []
        pulados = 0
        resgatados_maps = 0
        for r in cnpjs_pendentes:
            nome = r.get("nome_maps") or r.get("nome_fantasia") or ""
            if not nome or nome == r.get("razao_social", ""):
                # MEI sem nome fantasia: verificar se tem nome do Maps (match)
                nome_maps = r.get("nome_maps") or ""
                if nome_maps:
                    nome = nome_maps
                    resgatados_maps += 1
                else:
                    pulados += 1
                    log.info(f"[iFood] ⏭️ {r['cnpj']}: sem nome fantasia e sem match Maps - pulado")
                    continue
            items.append({"id": r["cnpj"], "nome": nome, "cidade": c})

        if pulados > 0:
            log.info(f"[iFood] ⏭️ {pulados} CNPJs pulados (sem nome fantasia e sem match Maps)")
        if resgatados_maps > 0:
            log.info(f"[iFood] 🔄 {resgatados_maps} MEIs resgatados via nome do Maps")

        if not items:
            log.warning(f"[iFood] ⚠️ Nenhum CNPJ com nome válido para iFood em {c}/{u}")
            continue

        log.info(f"[iFood] 🛵 {len(items)} CNPJs de {c}/{u} para verificar no iFood")
        if input("Continuar? [S/n]: ").strip().lower() == "n":
            continue

        headless = input("Headless? [S/n]: ").strip().lower() != "n"
        resultados = await verificar_ifood_batch(items, headless)

        for res in resultados:
            atualizar_ifood_receita(
                res["id"], res["tem_ifood"], res["ifood_nome"], res["ifood_url"]
            )

        com = sum(1 for r in resultados if r["tem_ifood"])
        log.info(f"[iFood] ✅ {com}/{len(resultados)} no iFood em {c}/{u}")

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
        log.info(f"{'='*60}")
        log.info(f"  🔍 GOOGLE MAPS: {c}/{u}")
        log.info(f"{'='*60}")
        registrar_varredura(c, u)
        nomes_ja = nomes_restaurantes_cidade(c, u)
        if modo == "2":
            resultados = await scrape_cidade_simples(c, u, headless)
            if resultados:
                inseridos = inserir_restaurantes_batch(resultados)
                log.info(f"[DB] 💾 {inseridos} novos (de {len(resultados)} encontrados)")
        else:
            resultados = await scrape_restaurantes_cidade(
                c, u, headless,
                save_callback=inserir_restaurante,
                nomes_existentes=nomes_ja,
            )
            if resultados:
                log.info(f"[DB] 💾 {len(resultados)} restaurantes extraídos (salvos incrementalmente)")
        if resultados:
            restaurantes_db = buscar_por_cidade(c, u)
            for r in restaurantes_db:
                if r["status"] == STATUS_PENDENTE:
                    atualizar_status(r["id"], STATUS_PROCESSADO)
            finalizar_varredura(c, u, len(resultados))
        else:
            log.warning(f"[WARN] Nenhum restaurante em {c}/{u}")
    input("\n[ENTER para continuar...]")


async def acao_maps_direcionado():
    """Busca direcionada no Maps: busca cada CNPJ detalhado pelo seu endereço."""
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    headless = input("\nHeadless? [S/n]: ").strip().lower() != "n"
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]

    for c, u in cidades:
        log.info(f"{'='*60}")
        log.info(f"  🎯 BUSCA MAPS DIRECIONADA: {c}/{u}")
        log.info(f"{'='*60}")

        def on_match(dados_maps, dados_cnpj):
            dados_cnpj["score_match"] = dados_maps.get("score_match", 0)
            rest_id = inserir_restaurante_e_vincular(dados_maps, dados_cnpj)
            if rest_id:
                log.info(f"[DB] 💾 Vinculado: {dados_cnpj.get('cnpj', '')} -> restaurante #{rest_id}")

        stats = await scrape_maps_direcionado(c, u, headless, callback=on_match)

        if stats["total"] > 0:
            log.info(f"[MAPS-DIR] ✅ {c}/{u}: {stats['encontrados']}/{stats['total']} encontrados")
        else:
            log.info(f"[MAPS-DIR] ℹ️ Nenhum CNPJ pendente em {c}/{u}")

    input("\n[ENTER para continuar...]")


async def acao_cruzar_enderecos():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return
    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]
    for c, u in cidades:
        log.info(f"{'='*60}")
        log.info(f"  🔗 CRUZAMENTO: {c}/{u}")
        log.info(f"  (Só restaurantes sem CNPJ)")
        log.info(f"{'='*60}")
        cruzar_cidade_completa(c, u)
        try:
            detectar_multi_restaurante(c, u)
        except Exception as e:
            log.warning(f"[MATCH] ⚠️ Multi-restaurante: {e}")
    input("\n[ENTER para continuar...]")


async def acao_pipeline_completo():
    cidade, uf = selecionar_cidade()
    if cidade == "__INVALIDO__":
        return

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              🚀 PIPELINE COMPLETO v3.0                      ║
║  A: Receita → B: cnpj.biz → C: Maps → D: Match → E: iFood  ║
║                  📱 TELEFONE DO PROPRIETÁRIO                 ║
╚══════════════════════════════════════════════════════════════╝""")

    print("\n[1] 🎯 Direcionada (busca CNPJ por endereço no Maps) (Recomendado)")
    print("[2] 🐢 Genérica completa (varredura geral + cruzamento)")
    print("[3] 🐇 Genérica rápida (varredura geral + cruzamento)")
    modo_maps = input("Modo Maps (1/2/3): ").strip()
    headless = input("Headless? [S/n]: ").strip().lower() != "n"
    if input("\n🚀 Iniciar? [S/n]: ").strip().lower() == "n":
        return

    cidades = [(cidade, uf)] if cidade else [(c["cidade"], c["uf"]) for c in CAPITAIS]

    for c, u in cidades:
        log.info(f"{'='*70}")
        log.info(f"  PIPELINE v3.0: {c}/{u}")
        log.info(f"{'='*70}")

        # A: Receita
        log.info(f"── A/5: Coleta Receita Federal ──")
        try:
            st = await coletar_cnpjs_cidade(c, u)
            log.info(f"[RECEITA] ✅ {st['novos']} novos | Base: {st['base_total']}")
        except Exception as e:
            log.warning(f"[RECEITA] ⚠️ {e}")

        # B: Detalhar via cnpj.biz (telefone proprietário!)
        log.info(f"── B/5: cnpj.biz (Tel Proprietário) ──")
        try:
            st = await detalhar_cnpjs_cidade(c, u, 0)
            log.info(f"[DETALHE] ✅ {st['detalhados']} detalhados via cnpj.biz")
        except Exception as e:
            log.warning(f"[DETALHE] ⚠️ {e}")

        # C: Maps (direcionada ou genérica)
        st_m = {"matched": 0, "taxa": "N/A"}

        if modo_maps == "1":
            # Busca direcionada por endereço CNPJ
            log.info(f"── C/5: Maps Direcionada (por endereço CNPJ) ──")

            def on_match_pipeline(dados_maps, dados_cnpj):
                dados_cnpj["score_match"] = dados_maps.get("score_match", 0)
                inserir_restaurante_e_vincular(dados_maps, dados_cnpj)

            st_maps = await scrape_maps_direcionado(c, u, headless, callback=on_match_pipeline)
            st_m = {
                "matched": st_maps.get("encontrados", 0),
                "taxa": f"{st_maps['encontrados']/st_maps['total']*100:.1f}%" if st_maps.get("total", 0) > 0 else "N/A",
            }
        else:
            # Busca genérica (varredura geral)
            log.info(f"── C/5: Google Maps (genérica) ──")
            registrar_varredura(c, u)
            nomes_ja = nomes_restaurantes_cidade(c, u)
            res = None
            if modo_maps == "3":
                res = await scrape_cidade_simples(c, u, headless)
                if res:
                    ins = inserir_restaurantes_batch(res)
                    log.info(f"[MAPS] 💾 {ins} novos salvos")
            else:
                res = await scrape_restaurantes_cidade(
                    c, u, headless,
                    save_callback=inserir_restaurante,
                    nomes_existentes=nomes_ja,
                )
                if res:
                    log.info(f"[MAPS] 💾 {len(res)} restaurantes (salvos incrementalmente)")
            if res:
                finalizar_varredura(c, u, len(res))
                for r in buscar_por_cidade(c, u):
                    if r["status"] == STATUS_PENDENTE:
                        atualizar_status(r["id"], STATUS_PROCESSADO)

            # D: Cruzamento (só para busca genérica)
            log.info(f"── D/5: Cruzamento Endereços ──")
            st_m = cruzar_cidade_completa(c, u)

        # Detectar sócios multi-restaurante
        try:
            detectar_multi_restaurante(c, u)
        except Exception as e:
            log.warning(f"[MATCH] ⚠️ Multi-restaurante: {e}")

        # E: iFood (com nome confirmado do Maps ou Receita)
        log.info(f"── E/5: iFood (nome confirmado) ──")
        try:
            cnpjs_ifood = obter_cnpjs_sem_ifood(c, u)
            if cnpjs_ifood:
                items = []
                pulados = 0
                resgatados_maps = 0
                for r in cnpjs_ifood:
                    nome = r.get("nome_maps") or r.get("nome_fantasia") or ""
                    if not nome or nome == r.get("razao_social", ""):
                        nome_maps = r.get("nome_maps") or ""
                        if nome_maps:
                            nome = nome_maps
                            resgatados_maps += 1
                        else:
                            pulados += 1
                            continue
                    items.append({"id": r["cnpj"], "nome": nome, "cidade": c})
                if pulados > 0:
                    log.info(f"[iFood] ⏭️ {pulados} CNPJs pulados (sem nome fantasia e sem match Maps)")
                if resgatados_maps > 0:
                    log.info(f"[iFood] 🔄 {resgatados_maps} MEIs resgatados via nome do Maps")
                if items:
                    res_if = await verificar_ifood_batch(items, headless)
                    for r in res_if:
                        atualizar_ifood_receita(r["id"], r["tem_ifood"], r["ifood_nome"], r["ifood_url"])
                    com = sum(1 for r in res_if if r["tem_ifood"])
                    log.info(f"[iFood] ✅ {com}/{len(res_if)} no iFood")
                else:
                    log.warning(f"[iFood] ⚠️ Nenhum CNPJ com nome válido para iFood")
            else:
                log.info(f"[iFood] ✅ Todos já verificados")
        except Exception as e:
            log.warning(f"[iFood] ⚠️ {e}")

        log.info(f"[PIPELINE] ✅ {c}/{u} PRONTO! Match: {st_m.get('matched', 0)} ({st_m.get('taxa', 'N/A')})")

    # Exportar
    log.info(f"── EXPORTAÇÃO ──")
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
              f"Matched: {stats_rec['matched']} | "
              f"Tel Prop: {stats_rec.get('com_tel_proprietario', 0)}")
        if stats['por_cidade']:
            print(f"\n  {'Cidade':<20} {'Maps':<7} {'iFood':<7} {'CNPJ':<7} {'TelProp':<7}")
            print(f"  {'─'*51}")
            for c in stats['por_cidade']:
                print(f"  {c['cidade']:<20} {c['total']:<7} {c['com_ifood']:<7} {c['com_cnpj']:<7} {c.get('com_tel_prop', 0):<7}")
        input("\n[ENTER para continuar...]")
        return

    dados = buscar_por_cidade(cidade, uf)
    if not dados:
        log.info(f"[INFO] Nenhum restaurante em {cidade}/{uf}.")
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
║               📈 ESTATÍSTICAS v3.0                    ║
╠════════════════════════════════════════════════════════╣
║  ── Google Maps ──                                    ║
║  Restaurantes: {stats['total']:>6}  Pendentes: {stats['pendentes']:>6}           ║
║  iFood: {stats['com_ifood']:>6}        CNPJ: {stats['com_cnpj']:>6}              ║
║  Sócios: {stats['total_socios']:>6}   Tel Prop: {stats.get('com_tel_proprietario', 0):>6}           ║
║                                                       ║
║  ── Receita Federal ──                                ║
║  CNPJs base: {stats_rec['total']:>6}   Detalhados: {stats_rec['detalhados']:>6}        ║
║  Matched: {stats_rec['matched']:>6}      Email: {stats_rec['com_email']:>6}             ║
║  Tel Prop: {stats_rec.get('com_tel_proprietario', 0):>6}    iFood: {stats_rec.get('com_ifood', 0):>6}             ║
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
            elif op == "C": await acao_varredura()
            elif op == "F": await acao_maps_direcionado()
            elif op == "D": await acao_cruzar_enderecos()
            elif op == "E": await acao_verificar_ifood()
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
            log.info("[INFO] Interrompido. Dados salvos.")
            input("[ENTER para voltar ao menu...]")
        except Exception as e:
            log.error(f"[ERRO] {e}")
            import traceback
            traceback.print_exc()
            input("[ENTER...]")


if __name__ == "__main__":
    asyncio.run(main())
