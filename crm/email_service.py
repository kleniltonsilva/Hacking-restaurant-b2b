"""
email_service.py - Integração com Resend API para envio de emails
Throttling: max 10 emails/segundo (respeitando rate limit Resend)
"""
import os
import json
import time
import re
from typing import Optional

import resend

from crm.database import (
    obter_lead, obter_email_template, registrar_interacao,
    atualizar_campanha_contadores, atualizar_status_campanha,
    buscar_leads_para_export, marcar_email_invalido,
    buscar_interacao_por_email_id,
    obter_configuracao, cidade_tem_delivery_verificado,
)
from crm.scoring import personalizar_abordagem
from crm.competitor_service import dados_mercado_cidade, concorrentes_bairro

resend.api_key = os.environ.get("RESEND_API_KEY", "")

FROM_EMAIL = os.environ.get("FROM_EMAIL", "contato@derekh.com.br")
FROM_NAME = os.environ.get("FROM_NAME", "Derekh Food")


# ============================================================
# VARIÁVEIS DE TEMPLATE
# ============================================================

def _extrair_variaveis(lead: dict) -> dict:
    """Extrai variáveis disponíveis para substituição em templates.
    Inclui dados de mercado e concorrentes para personalização."""
    personalizacao = personalizar_abordagem(lead)

    cidade = lead.get("cidade") or ""
    uf = lead.get("uf") or ""
    bairro = lead.get("bairro") or ""

    # Buscar nome_usuario das configurações
    nome_usuario = obter_configuracao("nome_usuario") or "Equipe Derekh"

    vars = {
        "nome_dono": personalizacao["nome_dono"] or "prezado(a)",
        "nome_restaurante": lead.get("nome_fantasia") or lead.get("razao_social") or "seu restaurante",
        "nome_usuario": nome_usuario,
        "razao_social": lead.get("razao_social") or "",
        "cidade": cidade,
        "uf": uf,
        "bairro": bairro,
        "rating": str(lead.get("rating") or ""),
        "total_avaliacoes": str(lead.get("total_reviews") or "0"),
        "capital_social": f"R$ {float(lead.get('capital_social') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "porte": lead.get("porte") or "",
        "cnpj": lead.get("cnpj") or "",
    }

    # Dados de mercado da cidade
    if cidade and uf:
        try:
            # Verificar se delivery foi verificado antes de mostrar concorrentes
            delivery_verificado = cidade_tem_delivery_verificado(cidade, uf)

            mercado = dados_mercado_cidade(cidade, uf)
            vars["total_restaurantes_cidade"] = str(mercado.get("total_restaurantes", 0))
            vars["total_com_delivery_cidade"] = str(mercado.get("com_algum_delivery", 0))
            vars["total_sem_delivery_cidade"] = str(mercado.get("sem_delivery", 0))
            vars["total_ifood_cidade"] = str(mercado.get("com_ifood", 0))

            if delivery_verificado:
                # Concorrentes do bairro com delivery
                concorrentes = concorrentes_bairro(lead.get("id", 0), limite=5)
                if concorrentes:
                    lista_html = ""
                    for c in concorrentes:
                        nome = c.get("nome_fantasia") or c.get("razao_social") or "—"
                        plataformas = []
                        if c.get("tem_ifood"): plataformas.append("iFood")
                        if c.get("tem_rappi"): plataformas.append("Rappi")
                        if c.get("tem_99food"): plataformas.append("99Food")
                        rating_str = f" | {c['rating']}★" if c.get("rating") else ""
                        lista_html += f'<li style="padding:4px 0;color:#374151;">{nome} — {", ".join(plataformas)}{rating_str}</li>'
                    vars["concorrentes_html"] = f'<ul style="list-style:none;padding:0;margin:8px 0;">{lista_html}</ul>'
                    vars["total_concorrentes_delivery"] = str(len(concorrentes))
                else:
                    vars["concorrentes_html"] = '<p style="color:#6b7280;">Nenhum concorrente com delivery encontrado no bairro.</p>'
                    vars["total_concorrentes_delivery"] = "0"
            else:
                vars["concorrentes_html"] = '<p style="color:#6b7280;">Verificação de delivery pendente para esta cidade.</p>'
                vars["total_concorrentes_delivery"] = "—"
        except Exception:
            vars["total_restaurantes_cidade"] = "—"
            vars["total_com_delivery_cidade"] = "—"
            vars["total_sem_delivery_cidade"] = "—"
            vars["total_ifood_cidade"] = "—"
            vars["concorrentes_html"] = ""
            vars["total_concorrentes_delivery"] = "—"

    return vars


def _substituir_variaveis(texto: str, variaveis: dict) -> str:
    """Substitui {variavel} no texto pelos valores reais."""
    for chave, valor in variaveis.items():
        texto = texto.replace("{" + chave + "}", str(valor))
    return texto


# ============================================================
# ENVIO
# ============================================================

def enviar_email(lead_id: int, template_id: int, campanha_id: int = None) -> dict:
    """Envia email para um lead usando um template.
    Retorna dict com status e message_id."""
    lead = obter_lead(lead_id)
    if not lead:
        return {"erro": "Lead não encontrado"}

    email_dest = lead.get("email")
    if not email_dest or not email_dest.strip():
        return {"erro": "Lead sem email"}

    if lead.get("email_invalido"):
        return {"erro": "Email marcado como inválido (bounced)"}

    template = obter_email_template(template_id)
    if not template:
        return {"erro": "Template não encontrado"}

    variaveis = _extrair_variaveis(lead)
    assunto = _substituir_variaveis(template["assunto"], variaveis)
    corpo = _substituir_variaveis(template["corpo_html"], variaveis)

    try:
        resultado = resend.Emails.send({
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [email_dest],
            "subject": assunto,
            "html": corpo,
        })

        message_id = resultado.get("id", "")

        # Registrar interação
        registrar_interacao(
            lead_id=lead_id,
            tipo="email",
            canal="email",
            conteudo=f"Assunto: {assunto}",
            resultado="enviado",
            email_message_id=message_id,
        )

        # Atualizar campanha se aplicável
        if campanha_id:
            atualizar_campanha_contadores(campanha_id, "total_enviados")

        return {"sucesso": True, "message_id": message_id}

    except Exception as e:
        return {"erro": str(e)}


def enviar_campanha(campanha_id: int, filtros: dict, template_id: int,
                    throttle_por_segundo: int = 10) -> dict:
    """Envia campanha para todos os leads que passam nos filtros.
    Throttle de N emails/segundo."""
    leads = buscar_leads_para_export(filtros)
    atualizar_status_campanha(campanha_id, "enviando")

    stats = {"enviados": 0, "erros": 0, "sem_email": 0}
    batch_start = time.time()
    batch_count = 0

    for lead in leads:
        if not lead.get("email") or lead.get("email_invalido"):
            stats["sem_email"] += 1
            continue

        resultado = enviar_email(lead["id"], template_id, campanha_id)

        if resultado.get("sucesso"):
            stats["enviados"] += 1
        else:
            stats["erros"] += 1

        batch_count += 1

        # Throttling
        if batch_count >= throttle_por_segundo:
            elapsed = time.time() - batch_start
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            batch_start = time.time()
            batch_count = 0

    atualizar_status_campanha(campanha_id, "concluida")
    return stats


# ============================================================
# WEBHOOK RESEND
# ============================================================

def processar_webhook_resend(payload: dict) -> dict:
    """Processa webhook do Resend (opened, clicked, bounced, etc)."""
    event_type = payload.get("type", "")
    data = payload.get("data", {})
    email_id = data.get("email_id", "")

    if not email_id:
        return {"ignorado": "sem email_id"}

    interacao = buscar_interacao_por_email_id(email_id)
    if not interacao:
        return {"ignorado": "interação não encontrada"}

    lead_id = interacao.get("lead_id") or interacao.get("lead_id_fk")

    if event_type == "email.opened":
        # Encontrar campanha_id e incrementar abertos
        # (Simplificado: registrar como interação)
        registrar_interacao(lead_id, "email", "email", "Email aberto", "positivo")

    elif event_type == "email.clicked":
        registrar_interacao(lead_id, "email", "email", "Link clicado no email", "positivo")

    elif event_type == "email.bounced":
        marcar_email_invalido(lead_id)
        registrar_interacao(lead_id, "email", "email", "Email bounced", "negativo")

    elif event_type == "email.complained":
        registrar_interacao(lead_id, "email", "email", "Marcou como spam", "negativo")

    return {"processado": event_type, "lead_id": lead_id}


def preview_template(template_id: int, lead_id: int) -> dict:
    """Preview de template com variáveis de um lead real."""
    lead = obter_lead(lead_id)
    if not lead:
        return {"erro": "Lead não encontrado"}

    template = obter_email_template(template_id)
    if not template:
        return {"erro": "Template não encontrado"}

    variaveis = _extrair_variaveis(lead)
    return {
        "assunto": _substituir_variaveis(template["assunto"], variaveis),
        "corpo_html": _substituir_variaveis(template["corpo_html"], variaveis),
        "variaveis_disponiveis": list(variaveis.keys()),
    }
