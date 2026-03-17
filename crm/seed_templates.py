"""
seed_templates.py - Insere templates profissionais de email no banco
Uso: DATABASE_URL=postgres://... python -m crm.seed_templates
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm.database import init_pool, criar_email_template

# ============================================================
# ESTILO BASE (inline CSS para compatibilidade com clientes de email)
# ============================================================

WRAPPER_START = """
<div style="max-width:600px;margin:0 auto;font-family:'Segoe UI',Arial,sans-serif;color:#1f2937;line-height:1.6;">
  <!-- Header -->
  <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:32px 24px;border-radius:12px 12px 0 0;text-align:center;">
    <h1 style="color:#ffffff;font-size:22px;margin:0;font-weight:700;letter-spacing:-0.5px;">Derekh Food</h1>
    <p style="color:#c7d2fe;font-size:13px;margin:6px 0 0;">Delivery próprio para restaurantes</p>
  </div>
  <!-- Body -->
  <div style="background:#ffffff;padding:32px 24px;border:1px solid #e5e7eb;border-top:none;">
"""

WRAPPER_END = """
  </div>
  <!-- Footer -->
  <div style="background:#f9fafb;padding:20px 24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;text-align:center;">
    <p style="color:#9ca3af;font-size:11px;margin:0;">Derekh Food — Delivery próprio para restaurantes</p>
    <p style="color:#9ca3af;font-size:11px;margin:4px 0 0;">Recebeu por engano? <a href="#" style="color:#6366f1;">Cancelar inscrição</a></p>
  </div>
</div>
"""

CTA_BUTTON = """
<div style="text-align:center;margin:28px 0;">
  <a href="https://derekh.com.br?utm_source=email&utm_campaign={campanha}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-weight:600;font-size:15px;">
    {texto_botao}
  </a>
</div>
"""

HIGHLIGHT_BOX = """
<div style="background:#eef2ff;border-left:4px solid #4f46e5;padding:16px 20px;border-radius:0 8px 8px 0;margin:20px 0;">
  {conteudo}
</div>
"""

STAT_ROW = """
<div style="display:flex;background:#f9fafb;border-radius:8px;padding:12px 16px;margin:8px 0;">
  <div style="flex:1;text-align:center;">
    <div style="font-size:24px;font-weight:700;color:#4f46e5;">{valor1}</div>
    <div style="font-size:11px;color:#6b7280;margin-top:2px;">{label1}</div>
  </div>
  <div style="width:1px;background:#e5e7eb;margin:0 12px;"></div>
  <div style="flex:1;text-align:center;">
    <div style="font-size:24px;font-weight:700;color:#059669;">{valor2}</div>
    <div style="font-size:11px;color:#6b7280;margin-top:2px;">{label2}</div>
  </div>
  <div style="width:1px;background:#e5e7eb;margin:0 12px;"></div>
  <div style="flex:1;text-align:center;">
    <div style="font-size:24px;font-weight:700;color:#dc2626;">{valor3}</div>
    <div style="font-size:11px;color:#6b7280;margin-top:2px;">{label3}</div>
  </div>
</div>
"""


# ============================================================
# TEMPLATES
# ============================================================

TEMPLATES = [
    # 1. RELATÓRIO DE MERCADO — SEM DELIVERY
    {
        "nome": "Relatório de Mercado — Sem Delivery",
        "assunto": "{nome_restaurante}: {total_restaurantes_cidade} restaurantes em {cidade} e o delivery está mudando tudo",
        "segmento_alvo": "quente",
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Olá <strong>{nome_dono}</strong>,</p>

    <p>Eu analisei o mercado de restaurantes de <strong>{cidade}/{uf}</strong> e preparei um relatório rápido para o <strong>{nome_restaurante}</strong>.</p>

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">📊 Panorama do seu mercado</h2>

""" + STAT_ROW.format(
    valor1="{total_restaurantes_cidade}", label1="Restaurantes na cidade",
    valor2="{total_com_delivery_cidade}", label2="Com delivery ativo",
    valor3="{total_sem_delivery_cidade}", label3="Sem delivery"
) + """

""" + HIGHLIGHT_BOX.format(conteudo="""
  <p style="margin:0;font-size:14px;"><strong>O que isso significa para você?</strong></p>
  <p style="margin:8px 0 0;font-size:13px;color:#4b5563;">Restaurantes com delivery próprio faturam em média <strong>35% a mais</strong> do que os que dependem apenas do salão. Seus concorrentes já estão se movimentando.</p>
""") + """

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">🔥 Concorrentes com delivery na sua região</h2>

    {concorrentes_html}

    <p style="font-size:14px;color:#374151;margin-top:20px;">A Derekh cria seu delivery próprio em <strong>48 horas</strong>, sem comissão de 27% como o iFood cobra. O pedido vai direto para o seu caixa.</p>

    <p style="font-size:14px;color:#374151;">Preparei uma análise personalizada para o <strong>{nome_restaurante}</strong> com projeção de faturamento via delivery. É grátis, sem compromisso.</p>

""" + CTA_BUTTON.format(campanha="relatorio_mercado", texto_botao="Quero minha análise gratuita →") + """

    <p style="font-size:13px;color:#6b7280;">Abraço,<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },

    # 2. CONCORRENTES NO IFOOD — JÁ TEM IFOOD
    {
        "nome": "Seus concorrentes no iFood — Já tem iFood",
        "assunto": "{nome_restaurante}, quanto do seu faturamento fica com o iFood?",
        "segmento_alvo": "quente",
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Olá <strong>{nome_dono}</strong>,</p>

    <p>Vi que o <strong>{nome_restaurante}</strong> já está no iFood. Isso é ótimo — mostra que você entende a importância do delivery.</p>

    <p>Mas eu preciso te fazer uma pergunta direta:</p>

""" + HIGHLIGHT_BOX.format(conteudo="""
  <p style="margin:0;font-size:15px;color:#1f2937;"><strong>Você sabia que o iFood fica com até 27% de cada pedido?</strong></p>
  <p style="margin:8px 0 0;font-size:13px;color:#4b5563;">Em um mês com R$ 30.000 em pedidos, isso são <strong>R$ 8.100</strong> que saem do seu bolso. Em um ano: <strong>R$ 97.200</strong>.</p>
""") + """

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">💡 A alternativa inteligente</h2>

    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr>
        <td style="padding:10px;background:#fef2f2;border-radius:8px 0 0 0;text-align:center;width:50%;">
          <div style="font-size:13px;color:#991b1b;font-weight:600;">iFood</div>
          <div style="font-size:22px;font-weight:700;color:#dc2626;margin:4px 0;">27%</div>
          <div style="font-size:11px;color:#991b1b;">de comissão por pedido</div>
        </td>
        <td style="padding:10px;background:#ecfdf5;border-radius:0 8px 0 0;text-align:center;width:50%;">
          <div style="font-size:13px;color:#065f46;font-weight:600;">Derekh</div>
          <div style="font-size:22px;font-weight:700;color:#059669;margin:4px 0;">0%</div>
          <div style="font-size:11px;color:#065f46;">comissão — taxa fixa mensal</div>
        </td>
      </tr>
    </table>

    <p style="font-size:14px;color:#374151;">A Derekh não substitui o iFood — ela <strong>complementa</strong>. Você mantém o iFood para visibilidade e usa o Derekh para seus clientes recorrentes, sem pagar comissão.</p>

    <p style="font-size:14px;color:#374151;">Quero te mostrar quanto o <strong>{nome_restaurante}</strong> pode economizar por mês. É uma simulação de 2 minutos.</p>

""" + CTA_BUTTON.format(campanha="economia_ifood", texto_botao="Ver minha simulação de economia →") + """

    <p style="font-size:13px;color:#6b7280;">Abraço,<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },

    # 3. OPORTUNIDADE LOCAL — GENÉRICO COM DADOS DE MERCADO
    {
        "nome": "Oportunidade de Mercado — Dados Locais",
        "assunto": "📊 {nome_dono}, {total_restaurantes_cidade} restaurantes em {cidade}: onde está a sua oportunidade?",
        "segmento_alvo": None,
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Olá <strong>{nome_dono}</strong>,</p>

    <p>Fizemos um mapeamento completo do mercado de alimentação em <strong>{cidade}/{uf}</strong> e encontramos algo interessante:</p>

""" + STAT_ROW.format(
    valor1="{total_restaurantes_cidade}", label1="Restaurantes mapeados",
    valor2="{total_com_delivery_cidade}", label2="Já fazem delivery",
    valor3="{total_sem_delivery_cidade}", label3="Ainda sem delivery"
) + """

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">O que os dados mostram</h2>

    <div style="margin:16px 0;">
      <div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <span style="font-size:20px;margin-right:12px;">📈</span>
        <span style="font-size:14px;color:#374151;">Pedidos por delivery cresceram <strong>40%</strong> em 2025</span>
      </div>
      <div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <span style="font-size:20px;margin-right:12px;">💰</span>
        <span style="font-size:14px;color:#374151;">Ticket médio no delivery é <strong>23% maior</strong> que no salão</span>
      </div>
      <div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <span style="font-size:20px;margin-right:12px;">🏪</span>
        <span style="font-size:14px;color:#374151;">Restaurantes com delivery próprio retêm <strong>3x mais clientes</strong></span>
      </div>
      <div style="display:flex;align-items:center;padding:8px 0;">
        <span style="font-size:20px;margin-right:12px;">🔄</span>
        <span style="font-size:14px;color:#374151;">Cliente que pede direto volta <strong>67% mais vezes</strong></span>
      </div>
    </div>

""" + HIGHLIGHT_BOX.format(conteudo="""
  <p style="margin:0;font-size:14px;"><strong>Análise gratuita para o {nome_restaurante}</strong></p>
  <p style="margin:8px 0 0;font-size:13px;color:#4b5563;">Preparamos um relatório personalizado com projeção de faturamento via delivery, análise dos seus concorrentes e estratégia de lançamento. Tudo grátis.</p>
""") + """

""" + CTA_BUTTON.format(campanha="oportunidade_local", texto_botao="Solicitar minha análise gratuita →") + """

    <p style="font-size:13px;color:#6b7280;">Abraço,<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },

    # 4. RESTAURANTE NOVO (< 6 MESES)
    {
        "nome": "Boas-vindas Restaurante Novo",
        "assunto": "{nome_dono}, parabéns pelo {nome_restaurante}! 🎉 Presente de inauguração",
        "segmento_alvo": "novo",
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Olá <strong>{nome_dono}</strong>,</p>

    <p>Parabéns pela abertura do <strong>{nome_restaurante}</strong> em <strong>{cidade}/{uf}</strong>! 🎉</p>

    <p>Os primeiros meses de um restaurante são decisivos. E ter um canal de delivery próprio desde o início faz toda a diferença.</p>

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">🎁 Presente de inauguração</h2>

    <div style="background:#ecfdf5;border:2px dashed #059669;border-radius:12px;padding:24px;text-align:center;margin:20px 0;">
      <p style="font-size:13px;color:#065f46;margin:0;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Oferta exclusiva para novos restaurantes</p>
      <p style="font-size:28px;font-weight:800;color:#059669;margin:8px 0;">30 dias grátis</p>
      <p style="font-size:14px;color:#065f46;margin:0;">Setup completo + cardápio digital + delivery funcionando</p>
    </div>

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">Por que começar com delivery próprio?</h2>

    <div style="margin:16px 0;">
      <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#059669;">✓</strong> <span style="font-size:14px;color:#374151;">Receba pedidos desde o dia 1 — sem esperar aprovação do iFood</span>
      </div>
      <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#059669;">✓</strong> <span style="font-size:14px;color:#374151;">Sem comissão de 27% — seu lucro fica com você</span>
      </div>
      <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#059669;">✓</strong> <span style="font-size:14px;color:#374151;">Sua marca, seu cardápio, suas regras</span>
      </div>
      <div style="padding:8px 0;">
        <strong style="color:#059669;">✓</strong> <span style="font-size:14px;color:#374151;">Dados dos clientes são seus (não do marketplace)</span>
      </div>
    </div>

""" + CTA_BUTTON.format(campanha="restaurante_novo", texto_botao="Ativar meus 30 dias grátis →") + """

    <p style="font-size:13px;color:#6b7280;">Sucesso com o {nome_restaurante}!<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },

    # 5. PREMIUM — FORMAL COM ROI
    {
        "nome": "Proposta Premium — ROI e Controle",
        "assunto": "{nome_restaurante}: delivery próprio com ROI garantido",
        "segmento_alvo": "premium",
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Prezado(a) <strong>{nome_dono}</strong>,</p>

    <p>Entramos em contato com o <strong>{nome_restaurante}</strong> porque identificamos que seu estabelecimento se enquadra no perfil de operações que mais se beneficiam de um canal de delivery próprio.</p>

""" + HIGHLIGHT_BOX.format(conteudo="""
  <p style="margin:0;font-size:14px;"><strong>Perfil do {nome_restaurante}</strong></p>
  <p style="margin:8px 0 0;font-size:13px;color:#4b5563;">Capital social: {capital_social} | Porte: {porte} | Localização: {bairro}, {cidade}/{uf}</p>
""") + """

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">Projeção de Retorno</h2>

    <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:13px;">
      <tr style="background:#f9fafb;">
        <td style="padding:10px 16px;font-weight:600;color:#374151;">Cenário</td>
        <td style="padding:10px 16px;text-align:center;font-weight:600;color:#374151;">Pedidos/mês</td>
        <td style="padding:10px 16px;text-align:center;font-weight:600;color:#374151;">Economia vs iFood</td>
      </tr>
      <tr style="border-bottom:1px solid #e5e7eb;">
        <td style="padding:10px 16px;color:#6b7280;">Conservador</td>
        <td style="padding:10px 16px;text-align:center;">200</td>
        <td style="padding:10px 16px;text-align:center;color:#059669;font-weight:600;">R$ 2.700/mês</td>
      </tr>
      <tr style="border-bottom:1px solid #e5e7eb;">
        <td style="padding:10px 16px;color:#6b7280;">Moderado</td>
        <td style="padding:10px 16px;text-align:center;">500</td>
        <td style="padding:10px 16px;text-align:center;color:#059669;font-weight:600;">R$ 6.750/mês</td>
      </tr>
      <tr>
        <td style="padding:10px 16px;color:#6b7280;">Otimista</td>
        <td style="padding:10px 16px;text-align:center;">1.000</td>
        <td style="padding:10px 16px;text-align:center;color:#059669;font-weight:600;">R$ 13.500/mês</td>
      </tr>
    </table>

    <p style="font-size:11px;color:#9ca3af;margin-top:4px;">*Baseado em ticket médio de R$ 50 e comissão iFood de 27%.</p>

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">Diferenciais para operações como a sua</h2>

    <div style="margin:16px 0;">
      <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#4f46e5;">→</strong> <span style="font-size:14px;color:#374151;"><strong>Controle total</strong> — cardápio, preços, promoções, horários</span>
      </div>
      <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#4f46e5;">→</strong> <span style="font-size:14px;color:#374151;"><strong>Dados proprietários</strong> — base de clientes é sua, não do marketplace</span>
      </div>
      <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#4f46e5;">→</strong> <span style="font-size:14px;color:#374151;"><strong>Marca própria</strong> — app e site com a identidade do {nome_restaurante}</span>
      </div>
      <div style="padding:8px 0;">
        <strong style="color:#4f46e5;">→</strong> <span style="font-size:14px;color:#374151;"><strong>Integração</strong> — conecta com seu PDV e sistemas existentes</span>
      </div>
    </div>

""" + CTA_BUTTON.format(campanha="premium_roi", texto_botao="Agendar apresentação executiva →") + """

    <p style="font-size:13px;color:#6b7280;">Atenciosamente,<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },

    # 6. FOLLOW-UP — QUEM NÃO RESPONDEU
    {
        "nome": "Follow-up — Lembrete com dado novo",
        "assunto": "Atualização: {total_com_delivery_cidade} restaurantes em {cidade} já fazem delivery",
        "segmento_alvo": None,
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Olá <strong>{nome_dono}</strong>,</p>

    <p>Entrei em contato há alguns dias sobre delivery próprio para o <strong>{nome_restaurante}</strong>. Desde então, atualizamos nossos dados:</p>

""" + HIGHLIGHT_BOX.format(conteudo="""
  <p style="margin:0;font-size:14px;color:#1f2937;"><strong>📊 Atualização de mercado — {cidade}</strong></p>
  <p style="margin:8px 0 0;font-size:13px;color:#4b5563;"><strong>{total_com_delivery_cidade}</strong> restaurantes da cidade já estão em plataformas de delivery. Mais restaurantes estão aderindo a cada semana.</p>
""") + """

    <p style="font-size:14px;color:#374151;">Sei que o dia a dia do restaurante é corrido. Por isso, a Derekh faz tudo por você:</p>

    <div style="margin:16px 0;">
      <div style="padding:6px 0;"><span style="color:#059669;font-weight:700;">1.</span> <span style="font-size:14px;">Cadastramos seu cardápio</span></div>
      <div style="padding:6px 0;"><span style="color:#059669;font-weight:700;">2.</span> <span style="font-size:14px;">Criamos seu link de delivery</span></div>
      <div style="padding:6px 0;"><span style="color:#059669;font-weight:700;">3.</span> <span style="font-size:14px;">Você começa a receber pedidos</span></div>
    </div>

    <p style="font-size:14px;color:#374151;"><strong>Leva menos de 48h.</strong> E você pode testar sem compromisso.</p>

""" + CTA_BUTTON.format(campanha="followup", texto_botao="Quero testar sem compromisso →") + """

    <p style="font-size:13px;color:#6b7280;">Abraço,<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },

    # 7. REDE — MULTI-UNIDADE
    {
        "nome": "Proposta Rede — Multi-unidade",
        "assunto": "{nome_restaurante}: gestão centralizada de delivery para todas as unidades",
        "segmento_alvo": "rede",
        "corpo_html": WRAPPER_START + """
    <p style="font-size:16px;color:#374151;">Prezado(a) <strong>{nome_dono}</strong>,</p>

    <p>Identificamos que o <strong>{nome_restaurante}</strong> opera com múltiplas unidades. Redes como a sua têm uma oportunidade única com delivery próprio.</p>

    <h2 style="font-size:17px;color:#1f2937;border-bottom:2px solid #e5e7eb;padding-bottom:8px;margin-top:28px;">Vantagens para redes</h2>

    <div style="margin:16px 0;">
      <div style="padding:10px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#4f46e5;">🏢 Painel centralizado</strong>
        <p style="font-size:13px;color:#6b7280;margin:4px 0 0;">Gerencie todas as unidades em um único dashboard. Cardápio, preços e promoções sincronizados.</p>
      </div>
      <div style="padding:10px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#4f46e5;">📊 Relatórios por unidade</strong>
        <p style="font-size:13px;color:#6b7280;margin:4px 0 0;">Compare performance entre filiais. Identifique as melhores práticas e replique.</p>
      </div>
      <div style="padding:10px 0;border-bottom:1px solid #f3f4f6;">
        <strong style="color:#4f46e5;">💰 Economia em escala</strong>
        <p style="font-size:13px;color:#6b7280;margin:4px 0 0;">Quanto mais unidades, menor o custo por unidade. Sem comissão em nenhuma delas.</p>
      </div>
      <div style="padding:10px 0;">
        <strong style="color:#4f46e5;">🎯 Marketing unificado</strong>
        <p style="font-size:13px;color:#6b7280;margin:4px 0 0;">Promoções nacionais ou locais. Cupons por unidade. Programa de fidelidade da marca.</p>
      </div>
    </div>

""" + CTA_BUTTON.format(campanha="rede", texto_botao="Solicitar proposta para rede →") + """

    <p style="font-size:13px;color:#6b7280;">Atenciosamente,<br><strong>Equipe Derekh Food</strong></p>
""" + WRAPPER_END
    },
]


def seed():
    """Insere todos os templates no banco."""
    init_pool()
    print("[SEED] Inserindo templates profissionais...")
    for t in TEMPLATES:
        template_id = criar_email_template(
            nome=t["nome"],
            assunto=t["assunto"],
            corpo_html=t["corpo_html"],
            segmento_alvo=t["segmento_alvo"],
        )
        print(f"  ✓ [{template_id}] {t['nome']}")
    print(f"[SEED] {len(TEMPLATES)} templates inseridos com sucesso.")


if __name__ == "__main__":
    seed()
