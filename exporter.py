"""
exporter.py - Exportação de dados para Excel
v4.0 - Multi-delivery (iFood + Rappi + 99Food)

ABAS:
  1. Leads Receita: TODOS os CNPJs (com email, telefone, sócios) - O PRINCIPAL
  2. Leads Detalhados: só com dados completos
  3. Leads Premium (iFood): com contato + no iFood
  4. Com Contato: todos que tem email/telefone
  5. Com Sócios: leads com QSA
  6. Sem Delivery: NÃO está em nenhuma plataforma (oportunidade máxima!)
  7. Restaurantes Maps: dados do Google Maps
  8. Resumo por Cidade: estatísticas
"""
import json
import os
import re
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import EXPORT_DIR
from db_manager import buscar_todos_para_export, buscar_leads_receita, buscar_leads_detalhados, estatisticas_gerais
from logger import log


def _normalizar_data(data_str: str) -> str:
    """Normaliza formatos de data para DD/MM/YYYY.
    Converte '1994-03-01' (ISO) para '01/03/1994' e mantém 'DD/MM/YYYY' como está."""
    if not data_str:
        return ""
    # Formato ISO: YYYY-MM-DD
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', data_str.strip())
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    # Já está em DD/MM/YYYY
    if re.match(r'^\d{2}/\d{2}/\d{4}$', data_str.strip()):
        return data_str.strip()
    return data_str


def _estilizar_planilha(wb, ws):
    """Aplica estilos profissionais à planilha."""
    header_fill = PatternFill(start_color="1B5E20", end_color="1B5E20", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    cell_font = Font(name="Calibri", size=10)
    border = Border(
        left=Side(style="thin", color="D0D0D0"),
        right=Side(style="thin", color="D0D0D0"),
        top=Side(style="thin", color="D0D0D0"),
        bottom=Side(style="thin", color="D0D0D0"),
    )
    ifood_sim = PatternFill(start_color="C8E6C9", end_color="C8E6C9", fill_type="solid")
    ifood_nao = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")

    for col in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = cell_font
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)

        for col in range(1, ws.max_column + 1):
            header = ws.cell(row=1, column=col).value
            if header in ("Tem iFood", "Tem Rappi", "Tem 99Food"):
                cell = ws.cell(row=row, column=col)
                if cell.value == "SIM":
                    cell.fill = ifood_sim
                elif cell.value == "NÃO":
                    cell.fill = ifood_nao

    for col in range(1, ws.max_column + 1):
        max_length = 0
        col_letter = get_column_letter(col)
        for row in range(1, min(ws.max_row + 1, 100)):
            cell = ws.cell(row=row, column=col)
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[col_letter].width = max(adjusted_width, 12)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _extrair_socios_do_json(socios_json: str) -> str:
    """Extrai nomes dos sócios do campo socios_json."""
    if not socios_json or socios_json == "[]":
        return ""
    try:
        socios = json.loads(socios_json)
        return " | ".join(
            f"{s.get('nome', '')} ({s.get('qualificacao', '')})"
            for s in socios if s.get("nome")
        )
    except (json.JSONDecodeError, TypeError):
        return ""


def _preparar_leads_receita(dados: list) -> list:
    """Prepara registros de leads da Receita para DataFrame."""
    registros = []
    for d in dados:
        socios_str = _extrair_socios_do_json(d.get("socios_json", "[]"))

        telefones = []
        if d.get("telefone1"):
            telefones.append(d["telefone1"])
        if d.get("telefone2"):
            telefones.append(d["telefone2"])
        telefones_str = " | ".join(telefones)

        # Limpar razão social: remover prefixo numérico de MEIs (ex: "63.836.131 NOME")
        razao = d.get("razao_social", "")
        razao_limpa = re.sub(r'^[\d\.\-/\s]+', '', razao).strip() if razao else ""
        # Usar a versão limpa, mas se ficou vazio, manter original
        razao_final = razao_limpa if razao_limpa else razao

        # Normalizar CEP: adicionar traço se faltante (ex: "49000626" -> "49000-626")
        cep_raw = d.get("cep", "") or ""
        cep_limpo = re.sub(r'[^\d]', '', cep_raw)
        if len(cep_limpo) == 8:
            cep_formatado = f"{cep_limpo[:5]}-{cep_limpo[5:]}"
        else:
            cep_formatado = cep_raw

        # Normalizar endereço: converter UPPERCASE para Title Case
        endereco = d.get("endereco_completo", "") or ""
        if endereco and endereco == endereco.upper() and len(endereco) > 10:
            endereco = endereco.title()

        # Normalizar bairro
        bairro = d.get("bairro", "") or ""
        if bairro and bairro == bairro.upper() and len(bairro) > 3:
            bairro = bairro.title()

        # Contar plataformas de delivery
        tem_ifood = 1 if d.get("tem_ifood") else 0
        tem_rappi = 1 if d.get("tem_rappi") else 0
        tem_99food = 1 if d.get("tem_99food") else 0
        num_plataformas = tem_ifood + tem_rappi + tem_99food

        registros.append({
            "CNPJ": d.get("cnpj", ""),
            "Razão Social": razao_final,
            "Nome Fantasia": d.get("nome_fantasia", ""),
            "Tipo Negócio": d.get("tipo_negocio", ""),
            "Cidade": d.get("cidade", ""),
            "UF": d.get("uf", ""),
            "Email": d.get("email", ""),
            "Email Proprietário": d.get("email_proprietario", ""),
            "Tel. Receita Federal": telefones_str,
            "Tel. Proprietário": d.get("telefone_proprietario", ""),
            "Sócios": socios_str,
            "Endereço": endereco,
            "Bairro": bairro,
            "CEP": cep_formatado,
            "Capital Social": d.get("capital_social", ""),
            "Porte": d.get("porte", ""),
            "Natureza Jurídica": d.get("natureza_juridica", ""),
            "Tipo Empresa": d.get("tipo_empresa", ""),
            "Data Abertura": _normalizar_data(d.get("data_abertura", "")),
            "Data Opção Simples": d.get("data_opcao_simples", ""),
            "Data Situação Cadastral": d.get("data_situacao_cadastral", ""),
            "Simples Nacional": "SIM" if d.get("simples") else "NÃO",
            "MEI": "SIM" if d.get("mei") else "NÃO",
            "CNAE Principal": d.get("cnae_principal", ""),
            "Tem iFood": "SIM" if tem_ifood else "NÃO",
            "iFood Nome": d.get("ifood_nome", ""),
            "Tem Rappi": "SIM" if tem_rappi else "NÃO",
            "Rappi Nome": d.get("rappi_nome", ""),
            "Tem 99Food": "SIM" if tem_99food else "NÃO",
            "99Food Nome": d.get("food99_nome", ""),
            "Num Plataformas": num_plataformas,
            "Match Maps": "SIM" if d.get("matched") else "NÃO",
            "Nome Maps": d.get("nome_maps", ""),
            "Score Match": f"{d.get('score_match', 0):.0%}" if d.get("score_match") else "",
            "Fonte": d.get("fonte_detalhamento", ""),
            "Multi-Restaurante": "SIM" if d.get("multi_restaurante") else "",
        })
    return registros


def exportar_excel(cidade: str = None, uf: str = None) -> str:
    """
    Exporta dados para Excel com formatação profissional.

    ABAS:
    - Leads Receita: TODOS os CNPJs detalhados (principal!)
    - Restaurantes Maps: dados do Google Maps
    - Leads Premium: com contato + sem iFood
    - Resumo: estatísticas
    """
    # Buscar TODOS os leads da Receita (base completa)
    leads_receita = buscar_leads_receita(cidade, uf)
    # Buscar apenas os detalhados (com dados completos) para abas filtradas
    leads_detalhados = buscar_leads_detalhados(cidade, uf)
    # Buscar restaurantes do Maps
    dados_maps = buscar_todos_para_export(cidade, uf)

    if not leads_receita and not dados_maps:
        log.warning("[EXPORT] ⚠️ Nenhum dado para exportar.")
        return ""

    # Nome do arquivo
    if cidade and uf:
        nome_arquivo = f"restaurantes_{cidade.replace(' ', '_')}_{uf}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    else:
        nome_arquivo = f"restaurantes_COMPLETO_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

    caminho = os.path.join(EXPORT_DIR, nome_arquivo)

    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:

        # === ABA 1: LEADS RECEITA (TODOS os CNPJs - nenhum lead perdido) ===
        if leads_receita:
            registros_receita = _preparar_leads_receita(leads_receita)
            df_receita = pd.DataFrame(registros_receita)
            df_receita.to_excel(writer, sheet_name="Leads Receita", index=False)

        # === ABA 2: LEADS DETALHADOS (só com dados completos) ===
        if leads_detalhados:
            registros_detalhados = _preparar_leads_receita(leads_detalhados)
            df_detalhados = pd.DataFrame(registros_detalhados)
            df_detalhados.to_excel(writer, sheet_name="Leads Detalhados", index=False)

            # === ABA: Leads Premium (tem contato + COM iFood = tem delivery!) ===
            df_premium = df_detalhados[
                (
                    (df_detalhados["Email"].fillna("") != "") |
                    (df_detalhados["Tel. Receita Federal"].fillna("") != "") |
                    (df_detalhados["Tel. Proprietário"].fillna("") != "")
                ) &
                (df_detalhados["Tem iFood"] == "SIM")
            ]
            if not df_premium.empty:
                df_premium.to_excel(writer, sheet_name="Leads Premium (iFood)", index=False)

            # === ABA: Todos com contato (independente de iFood) ===
            df_com_contato = df_detalhados[
                (df_detalhados["Email"].fillna("") != "") |
                (df_detalhados["Tel. Receita Federal"].fillna("") != "") |
                (df_detalhados["Tel. Proprietário"].fillna("") != "") |
                (df_detalhados["Email Proprietário"].fillna("") != "")
            ]
            if not df_com_contato.empty:
                df_com_contato.to_excel(writer, sheet_name="Com Contato", index=False)

            # === ABA: Leads com Sócios ===
            df_com_socios = df_detalhados[df_detalhados["Sócios"].fillna("") != ""]
            if not df_com_socios.empty:
                df_com_socios.to_excel(writer, sheet_name="Com Sócios", index=False)

            # === ABA: Sem Delivery (nao esta em NENHUMA plataforma) ===
            df_sem_delivery = df_detalhados[
                (df_detalhados["Tem iFood"] == "NÃO") &
                (df_detalhados["Tem Rappi"] == "NÃO") &
                (df_detalhados["Tem 99Food"] == "NÃO")
            ]
            if not df_sem_delivery.empty:
                df_sem_delivery.to_excel(writer, sheet_name="Sem Delivery", index=False)

        # === ABA 2: RESTAURANTES MAPS ===
        if dados_maps:
            registros_maps = []
            for d in dados_maps:
                registros_maps.append({
                    "Nome": d["nome"],
                    "Cidade": d["cidade"],
                    "UF": d["uf"],
                    "Endereço": d["endereco"],
                    "Tel. Google Maps": d["telefone"],
                    "Website": d["website"],
                    "Rating": d["rating"],
                    "Avaliações": d["total_reviews"],
                    "Categoria": d["categoria"],
                    "Tem iFood": "SIM" if d["tem_ifood"] else "NÃO",
                    "CNPJ": d.get("cnpj", ""),
                    "Razão Social": d.get("razao_social", ""),
                    "Email (Receita)": d.get("email_receita", ""),
                    "Tel. Receita Federal": d.get("telefones_receita", ""),
                    "Tel. Proprietário": d.get("telefone_proprietario", ""),
                    "Sócios": d.get("socios_nomes", ""),
                    "Score Confiança": f"{d.get('score_confianca', 0):.0%}" if d.get("score_confianca") else "",
                    "Google Maps URL": d.get("google_maps_url", ""),
                    "Status": d["status"],
                })

            df_maps = pd.DataFrame(registros_maps)
            df_maps.to_excel(writer, sheet_name="Restaurantes Maps", index=False)

        # === ABA: RESUMO ===
        resumo_data = []
        if leads_receita:
            for d in leads_receita:
                cidade_r = d.get("cidade", "")
                uf_r = d.get("uf", "")
                resumo_data.append({"Cidade": cidade_r, "UF": uf_r, "Fonte": "Receita"})
        if dados_maps:
            for d in dados_maps:
                resumo_data.append({"Cidade": d["cidade"], "UF": d["uf"], "Fonte": "Maps"})

        if resumo_data:
            df_resumo_raw = pd.DataFrame(resumo_data)
            resumo = df_resumo_raw.groupby(["Cidade", "UF", "Fonte"]).size().reset_index(name="Total")
            resumo_pivot = resumo.pivot_table(
                index=["Cidade", "UF"], columns="Fonte", values="Total", fill_value=0
            ).reset_index()
            resumo_pivot.to_excel(writer, sheet_name="Resumo", index=False)

    # Aplicar estilos
    wb = load_workbook(caminho)
    for ws_name in wb.sheetnames:
        _estilizar_planilha(wb, wb[ws_name])
    wb.save(caminho)

    total_leads = len(leads_receita) if leads_receita else 0
    total_detalhados = len(leads_detalhados) if leads_detalhados else 0
    total_maps = len(dados_maps) if dados_maps else 0

    log.info(f"[EXPORT] ✅ Excel exportado: {caminho}")
    log.info(f"[EXPORT] 📊 Leads Receita (TODOS): {total_leads} | Detalhados: {total_detalhados} | Maps: {total_maps}")

    return caminho


def exportar_csv(cidade: str = None, uf: str = None) -> str:
    """Exporta leads da Receita em formato CSV (mais leve)."""
    leads = buscar_leads_receita(cidade, uf)

    if not leads:
        log.warning("[EXPORT] ⚠️ Nenhum dado para exportar.")
        return ""

    registros = _preparar_leads_receita(leads)
    df = pd.DataFrame(registros)

    if cidade and uf:
        nome_arquivo = f"leads_{cidade.replace(' ', '_')}_{uf}.csv"
    else:
        nome_arquivo = "leads_COMPLETO.csv"

    caminho = os.path.join(EXPORT_DIR, nome_arquivo)
    df.to_csv(caminho, index=False, encoding="utf-8-sig")

    log.info(f"[EXPORT] ✅ CSV exportado: {caminho}")
    return caminho
