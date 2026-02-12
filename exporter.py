"""
exporter.py - Exportação de dados para Excel
Gera planilhas profissionais organizadas por cidade com todos os dados coletados.
"""
import os
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import EXPORT_DIR
from db_manager import buscar_todos_para_export, estatisticas_gerais


def _estilizar_planilha(wb, ws):
    """Aplica estilos profissionais à planilha."""
    # Cores
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
    
    # Estilizar headers
    for col in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    
    # Estilizar dados
    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = cell_font
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        
        # Colorir coluna iFood
        # Encontrar coluna do iFood
        for col in range(1, ws.max_column + 1):
            if ws.cell(row=1, column=col).value == "Tem iFood":
                ifood_cell = ws.cell(row=row, column=col)
                if ifood_cell.value == "SIM":
                    ifood_cell.fill = ifood_sim
                elif ifood_cell.value == "NÃO":
                    ifood_cell.fill = ifood_nao
    
    # Auto-ajustar largura das colunas
    for col in range(1, ws.max_column + 1):
        max_length = 0
        col_letter = get_column_letter(col)
        
        for row in range(1, min(ws.max_row + 1, 100)):  # Checar primeiras 100 linhas
            cell = ws.cell(row=row, column=col)
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        
        adjusted_width = min(max_length + 2, 50)  # Max 50 caracteres
        ws.column_dimensions[col_letter].width = max(adjusted_width, 12)
    
    # Congelar primeira linha
    ws.freeze_panes = "A2"
    
    # Filtros automáticos
    ws.auto_filter.ref = ws.dimensions


def exportar_excel(cidade: str = None, uf: str = None) -> str:
    """
    Exporta dados para Excel com formatação profissional.
    
    Args:
        cidade: Filtrar por cidade (None = todas)
        uf: Filtrar por UF (None = todas)
    
    Returns:
        Caminho do arquivo Excel gerado
    """
    dados = buscar_todos_para_export(cidade, uf)
    
    if not dados:
        print("[EXPORT] ⚠️ Nenhum dado para exportar.")
        return ""
    
    # Preparar dados para o DataFrame
    registros = []
    for d in dados:
        registros.append({
            "Nome": d["nome"],
            "Cidade": d["cidade"],
            "UF": d["uf"],
            "Endereço": d["endereco"],
            "Telefone": d["telefone"],
            "Website": d["website"],
            "Rating": d["rating"],
            "Avaliações": d["total_reviews"],
            "Categoria": d["categoria"],
            "Tem iFood": "SIM" if d["tem_ifood"] else "NÃO",
            "iFood Nome": d.get("ifood_nome", ""),
            "iFood URL": d.get("ifood_url", ""),
            "CNPJ": d.get("cnpj", ""),
            "Razão Social": d.get("razao_social", ""),
            "Nome Fantasia": d.get("nome_fantasia", ""),
            "Situação Cadastral": d.get("situacao_cadastral", ""),
            "Data Abertura": d.get("data_abertura", ""),
            "Capital Social": d.get("capital_social", ""),
            "Email (Receita)": d.get("email_receita", ""),
            "Telefone (Receita)": d.get("telefones_receita", ""),
            "Porte": d.get("porte_empresa", ""),
            "Simples Nacional": "SIM" if d.get("simples") else "NÃO",
            "Score Confiança": f"{d.get('score_confianca', 0):.0%}" if d.get("score_confianca") else "",
            "Sócios": d.get("socios_nomes", ""),
            "Qualificações": d.get("socios_qualificacoes", ""),
            "Google Maps URL": d.get("google_maps_url", ""),
            "Status": d["status"],
            "Data Coleta": d["data_coleta"],
        })
    
    df = pd.DataFrame(registros)
    
    # Nome do arquivo
    if cidade and uf:
        nome_arquivo = f"restaurantes_{cidade.replace(' ', '_')}_{uf}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    else:
        nome_arquivo = f"restaurantes_COMPLETO_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    
    caminho = os.path.join(EXPORT_DIR, nome_arquivo)
    
    # Salvar Excel
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        # Aba principal com todos os dados
        df.to_excel(writer, sheet_name="Restaurantes", index=False)
        
        # Aba de resumo por cidade
        if not cidade:
            resumo = df.groupby(["Cidade", "UF"]).agg(
                Total=("Nome", "count"),
                Com_iFood=("Tem iFood", lambda x: (x == "SIM").sum()),
                Sem_iFood=("Tem iFood", lambda x: (x == "NÃO").sum()),
                Com_CNPJ=("CNPJ", lambda x: (x != "").sum()),
                Com_Telefone=("Telefone", lambda x: (x != "").sum()),
                Com_Website=("Website", lambda x: (x != "").sum()),
            ).reset_index()
            resumo.to_excel(writer, sheet_name="Resumo por Cidade", index=False)
        
        # Aba só com restaurantes que TÊM iFood
        df_ifood = df[df["Tem iFood"] == "SIM"]
        if not df_ifood.empty:
            df_ifood.to_excel(writer, sheet_name="Com iFood", index=False)
        
        # Aba só com restaurantes que NÃO têm iFood (oportunidade!)
        df_sem_ifood = df[df["Tem iFood"] == "NÃO"]
        if not df_sem_ifood.empty:
            df_sem_ifood.to_excel(writer, sheet_name="Sem iFood (Oportunidade)", index=False)
    
    # Aplicar estilos
    wb = load_workbook(caminho)
    for ws_name in wb.sheetnames:
        _estilizar_planilha(wb, wb[ws_name])
    wb.save(caminho)
    
    print(f"[EXPORT] ✅ Excel exportado: {caminho}")
    print(f"[EXPORT] 📊 Total de registros: {len(registros)}")
    
    return caminho


def exportar_csv(cidade: str = None, uf: str = None) -> str:
    """Exporta dados em formato CSV (mais leve)."""
    dados = buscar_todos_para_export(cidade, uf)
    
    if not dados:
        print("[EXPORT] ⚠️ Nenhum dado para exportar.")
        return ""
    
    registros = []
    for d in dados:
        registros.append({
            "Nome": d["nome"],
            "Cidade": d["cidade"],
            "UF": d["uf"],
            "Endereco": d["endereco"],
            "Telefone": d["telefone"],
            "Website": d["website"],
            "Rating": d["rating"],
            "Tem_iFood": "SIM" if d["tem_ifood"] else "NAO",
            "CNPJ": d.get("cnpj", ""),
            "Razao_Social": d.get("razao_social", ""),
            "Socios": d.get("socios_nomes", ""),
            "Google_Maps_URL": d.get("google_maps_url", ""),
        })
    
    df = pd.DataFrame(registros)
    
    if cidade and uf:
        nome_arquivo = f"restaurantes_{cidade.replace(' ', '_')}_{uf}.csv"
    else:
        nome_arquivo = "restaurantes_COMPLETO.csv"
    
    caminho = os.path.join(EXPORT_DIR, nome_arquivo)
    df.to_csv(caminho, index=False, encoding="utf-8-sig")
    
    print(f"[EXPORT] ✅ CSV exportado: {caminho}")
    return caminho
