"""
whatsapp_service.py - Geração de links wa.me personalizados
Validação de dados + seleção inteligente de templates
"""
import re
import json
from urllib.parse import quote

from crm.database import obter_lead, obter_configuracao, cidade_tem_delivery_verificado
from crm.scoring import personalizar_abordagem, avaliar_qualidade_dados
from crm.models import WHATSAPP_TEMPLATES


def _limpar_telefone(tel: str) -> str:
    """Remove caracteres não numéricos do telefone."""
    if not tel:
        return ""
    return re.sub(r"\D", "", tel)


def _extrair_ddd_telefone(tel_limpo: str) -> str:
    """Extrai número formatado 55+DDD+número para wa.me."""
    if not tel_limpo:
        return ""
    # Se já começa com 55, usar como está
    if tel_limpo.startswith("55") and len(tel_limpo) >= 12:
        return tel_limpo
    # Se tem 10-11 dígitos (DDD + número), adicionar 55
    if len(tel_limpo) >= 10:
        return "55" + tel_limpo
    return ""


def _sugerir_template(qualidade: dict) -> str:
    """Auto-seleciona o melhor template baseado na qualidade dos dados."""
    if qualidade["tem_maps"]:
        return "primeiro_contato"
    return "primeiro_contato_basico"


def listar_templates_para_lead(lead_id: int) -> list:
    """Retorna templates com flag 'disponivel' baseado nos dados do lead."""
    lead = obter_lead(lead_id)
    if not lead:
        return []

    cidade = lead.get("cidade") or ""
    uf = lead.get("uf") or ""
    delivery_ok = cidade_tem_delivery_verificado(cidade, uf) if cidade and uf else False
    qualidade = avaliar_qualidade_dados(lead, delivery_ok)

    resultado = []
    for key, tpl in WHATSAPP_TEMPLATES.items():
        requer = tpl.get("requer", [])
        disponivel = all(qualidade.get(r, False) for r in requer)
        resultado.append({
            "key": key,
            "nome": tpl["nome"],
            "mensagem": tpl["mensagem"],
            "disponivel": disponivel,
            "requer": requer,
        })
    return resultado


def gerar_link_whatsapp(lead_id: int, template_key: str = "primeiro_contato",
                         usar_tel_proprietario: bool = False) -> dict:
    """Gera link wa.me personalizado para um lead.
    Retorna dict com link, mensagem_preview, telefone.
    Valida requisitos do template antes de gerar."""
    lead = obter_lead(lead_id)
    if not lead:
        return {"erro": "Lead não encontrado"}

    # Escolher telefone
    if usar_tel_proprietario and lead.get("telefone_proprietario"):
        tel_raw = lead["telefone_proprietario"]
    elif lead.get("telefone1"):
        tel_raw = lead["telefone1"]
    elif lead.get("telefone_proprietario"):
        tel_raw = lead["telefone_proprietario"]
    else:
        return {"erro": "Lead sem telefone"}

    tel_limpo = _limpar_telefone(tel_raw)
    tel_formatado = _extrair_ddd_telefone(tel_limpo)

    if not tel_formatado:
        return {"erro": f"Telefone inválido: {tel_raw}"}

    # Template de mensagem
    template = WHATSAPP_TEMPLATES.get(template_key)
    if not template:
        return {"erro": f"Template '{template_key}' não encontrado"}

    # Validar requisitos do template
    cidade = lead.get("cidade") or ""
    uf = lead.get("uf") or ""
    delivery_ok = cidade_tem_delivery_verificado(cidade, uf) if cidade and uf else False
    qualidade = avaliar_qualidade_dados(lead, delivery_ok)
    requer = template.get("requer", [])
    requisitos_faltando = [r for r in requer if not qualidade.get(r, False)]

    if requisitos_faltando:
        sugerido = _sugerir_template(qualidade)
        return {
            "erro": f"Template '{template_key}' requer dados indisponíveis: {', '.join(requisitos_faltando)}",
            "template_sugerido": sugerido,
            "template_sugerido_nome": WHATSAPP_TEMPLATES[sugerido]["nome"],
        }

    # Buscar nome_usuario das configurações
    nome_usuario = obter_configuracao("nome_usuario") or "Equipe Derekh"

    # Personalizar variáveis
    personalizacao = personalizar_abordagem(lead)
    variaveis = {
        "nome_dono": personalizacao["nome_dono"] or "prezado(a)",
        "nome_restaurante": lead.get("nome_fantasia") or lead.get("razao_social") or "seu restaurante",
        "nome_usuario": nome_usuario,
        "rating": str(lead.get("rating") or ""),
        "total_avaliacoes": str(lead.get("total_reviews") or "0"),
        "cidade": lead.get("cidade") or "",
    }

    mensagem = template["mensagem"]
    for chave, valor in variaveis.items():
        mensagem = mensagem.replace("{" + chave + "}", valor)

    link = f"https://wa.me/{tel_formatado}?text={quote(mensagem)}"

    return {
        "link": link,
        "mensagem_preview": mensagem,
        "telefone": tel_raw,
        "telefone_formatado": tel_formatado,
        "template_nome": template["nome"],
    }


def listar_templates_whatsapp() -> list:
    """Retorna lista de templates disponíveis."""
    return [
        {"key": k, "nome": v["nome"], "mensagem": v["mensagem"],
         "requer": v.get("requer", [])}
        for k, v in WHATSAPP_TEMPLATES.items()
    ]
