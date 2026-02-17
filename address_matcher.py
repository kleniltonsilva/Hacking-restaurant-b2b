"""
address_matcher.py - Motor de Cruzamento de Endereços
v1.0 - Cruza endereços da Receita Federal com endereços do Google Maps

LÓGICA CENTRAL:
  Para cada restaurante do Google Maps, procura na base de CNPJs da Receita
  um estabelecimento cujo endereço seja compatível. Se encontrar com score
  acima do mínimo → é o mesmo lugar → match confirmado.

ALGORITMO DE SIMILARIDADE:
  1. Normaliza ambos endereços (remove acentos, expande abreviações)
  2. Compara número do logradouro (peso 40%) → se diferente, descarta
  3. Compara nome da rua normalizado (peso 40%)
  4. Compara bairro (peso 20%)
  5. Score mínimo para match: 0.55

POR QUE FUNCIONA:
  - Na Receita Federal: "RUA PRESIDENTE CARLOS CAVALCANTI, 1222"
  - No Google Maps:     "R. Pres. Carlos Cavalcanti, 1222"
  - Após normalização:  "rua presidente carlos cavalcanti 1222" = MATCH
"""
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from config import DB_PATH
from logger import log


# ============================================================
# NORMALIZAÇÃO DE ENDEREÇOS
# ============================================================

# Abreviações comuns → forma expandida
ABREVIACOES = {
    r"\br\.?\b": "rua",
    r"\bav\.?\b": "avenida",
    r"\bal\.?\b": "alameda",
    r"\btv\.?\b": "travessa",
    r"\bpç\.?\b": "praca",
    r"\bpca\.?\b": "praca",
    r"\bpraca\b": "praca",
    r"\bpres\.?\b": "presidente",
    r"\bprof\.?\b": "professor",
    r"\bprofessora\b": "professor",
    r"\bdr\.?\b": "doutor",
    r"\bdra\.?\b": "doutor",
    r"\bdoutor\b": "doutor",
    r"\bdoutora\b": "doutor",
    r"\beng\.?\b": "engenheiro",
    r"\bengenheira\b": "engenheiro",
    r"\bgen\.?\b": "general",
    r"\bcap\.?\b": "capitao",
    r"\bcapitao\b": "capitao",
    r"\bcap\b": "capitao",
    r"\bsgto\.?\b": "sargento",
    r"\bcme\.?\b": "comendador",
    r"\bcomend\.?\b": "comendador",
    r"\bbar\.?\b": "barao",
    r"\bbarao\b": "barao",
    r"\bsta\.?\b": "santa",
    r"\bsto\.?\b": "santo",
    r"\bs/n\b": "",
    r"\bsn\b": "",
    r"\bn\.?\b": "",
    r"\bnr\.?\b": "",
    r"\bno\.?\b": "",
    r"\bnº\.?\b": "",
    r"\blt\.?\b": "lote",
    r"\bqd\.?\b": "quadra",
    r"\bconj\.?\b": "conjunto",
    r"\bsl\.?\b": "sala",
    r"\blj\.?\b": "loja",
    r"\band\.?\b": "andar",
    r"\bbl\.?\b": "bloco",
    r"\bed\.?\b": "edificio",
    r"\bedf\.?\b": "edificio",
    r"\brod\.?\b": "rodovia",
    r"\bbr\.?\b": "br",
    r"\best\.?\b": "estrada",
    r"\blgo\.?\b": "largo",
    r"\bjd\.?\b": "jardim",
    r"\bvl\.?\b": "vila",
    r"\bpq\.?\b": "parque",
    r"\bres\.?\b": "residencial",
    r"\bcond\.?\b": "condominio",
}

# Palavras que não contribuem para similaridade
STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "em", "no", "na",
    "nos", "nas", "com", "para", "por", "ao", "aos", "um", "uma",
}


def remover_acentos(texto: str) -> str:
    """Remove acentos e caracteres especiais."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_endereco(endereco: str) -> str:
    """
    Normaliza um endereço para comparação.
    Remove acentos, expande abreviações, remove stopwords.
    """
    if not endereco:
        return ""

    texto = remover_acentos(endereco.lower().strip())

    # Remover pontuação exceto números
    texto = re.sub(r'[,\.\-/\\;:()"\']', " ", texto)

    # Expandir abreviações
    for padrao, expansao in ABREVIACOES.items():
        texto = re.sub(padrao, expansao, texto, flags=re.IGNORECASE)

    # Remover stopwords
    palavras = texto.split()
    palavras = [p for p in palavras if p not in STOPWORDS and len(p) > 0]

    # Limpar espaços extras
    return " ".join(palavras).strip()


def extrair_numero(endereco: str) -> str:
    """
    Extrai o número do logradouro de um endereço.
    "Rua Tal, 1440" → "1440"
    "Av. Batel, 1440 - Loja 2" → "1440"
    """
    if not endereco:
        return ""

    # Padrão 1: número depois de vírgula
    match = re.search(r',\s*(\d+)', endereco)
    if match:
        return match.group(1)

    # Padrão 2: número isolado (não é CEP - 8 dígitos, nem ano)
    numeros = re.findall(r'\b(\d{1,5})\b', endereco)
    for n in numeros:
        num = int(n)
        if 1 <= num <= 99999 and len(n) <= 5:
            return n

    return ""


def extrair_logradouro(endereco: str) -> str:
    """
    Extrai apenas o nome da rua (sem número, complemento, bairro).
    "Rua Presidente Carlos Cavalcanti, 1222 - Centro" → "rua presidente carlos cavalcanti"
    """
    if not endereco:
        return ""

    texto = remover_acentos(endereco.lower().strip())

    # Cortar no primeiro número ou vírgula
    match = re.match(r'^([^,\d]+)', texto)
    if match:
        texto = match.group(1).strip()

    # Expandir abreviações
    for padrao, expansao in ABREVIACOES.items():
        texto = re.sub(padrao, expansao, texto, flags=re.IGNORECASE)

    # Remover stopwords e limpar
    palavras = texto.split()
    palavras = [p for p in palavras if p not in STOPWORDS and len(p) > 1]

    return " ".join(palavras).strip()


# ============================================================
# MOTOR DE SIMILARIDADE
# ============================================================

def similaridade_texto(texto1: str, texto2: str) -> float:
    """Calcula similaridade entre dois textos normalizados (0.0 a 1.0)."""
    if not texto1 or not texto2:
        return 0.0
    return SequenceMatcher(None, texto1, texto2).ratio()


def calcular_score_endereco(endereco_maps: str, endereco_receita: str,
                             numero_receita: str = "", bairro_receita: str = "") -> float:
    """
    Calcula score de similaridade entre endereço do Google Maps e da Receita Federal.

    Pesos:
    - Número do logradouro: 40% (decisivo — se não bate, é outro lugar)
    - Nome da rua: 40%
    - Bairro: 20%

    Returns:
        float: 0.0 a 1.0
    """
    if not endereco_maps or not endereco_receita:
        return 0.0

    # Extrair componentes do endereço do Maps
    numero_maps = extrair_numero(endereco_maps)
    logradouro_maps = extrair_logradouro(endereco_maps)
    logradouro_maps_norm = normalizar_endereco(logradouro_maps)

    # Montar endereço completo da Receita para extração
    endereco_receita_completo = endereco_receita
    if numero_receita:
        endereco_receita_completo = f"{endereco_receita}, {numero_receita}"

    numero_rec = numero_receita or extrair_numero(endereco_receita_completo)
    logradouro_receita = extrair_logradouro(endereco_receita)
    logradouro_receita_norm = normalizar_endereco(logradouro_receita)

    # Também normalizar o endereço completo do Maps para comparação global
    maps_norm = normalizar_endereco(endereco_maps)

    # --- SCORE DO NÚMERO (40%) ---
    score_numero = 0.0
    if numero_maps and numero_rec:
        if numero_maps == numero_rec:
            score_numero = 1.0
        else:
            score_numero = 0.0  # Número diferente = provavelmente outro lugar
    elif not numero_maps and not numero_rec:
        score_numero = 0.5  # Ambos sem número (S/N) — neutro
    else:
        score_numero = 0.1  # Um tem e outro não

    # Se número é diferente, muito provavelmente não é o mesmo lugar
    if numero_maps and numero_rec and numero_maps != numero_rec:
        return 0.05  # Score mínimo, nem vale comparar o resto

    # --- SCORE DO LOGRADOURO (40%) ---
    score_logradouro = 0.0
    if logradouro_maps_norm and logradouro_receita_norm:
        score_logradouro = similaridade_texto(logradouro_maps_norm, logradouro_receita_norm)
    elif maps_norm and logradouro_receita_norm:
        # Fallback: comparar endereço completo do maps com logradouro da receita
        score_logradouro = similaridade_texto(maps_norm, logradouro_receita_norm) * 0.8

    # --- SCORE DO BAIRRO (20%) ---
    score_bairro = 0.0
    if bairro_receita:
        bairro_norm = normalizar_endereco(bairro_receita)
        # Verificar se o bairro aparece no endereço do Maps
        if bairro_norm and bairro_norm in maps_norm:
            score_bairro = 1.0
        elif bairro_norm:
            score_bairro = similaridade_texto(bairro_norm, maps_norm) * 0.5

    # --- SCORE FINAL ---
    score = (score_numero * 0.40) + (score_logradouro * 0.40) + (score_bairro * 0.20)

    return round(score, 4)


# ============================================================
# CRUZAMENTO: RESTAURANTES × CNPJs DA RECEITA
# ============================================================

def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cruzar_restaurante_com_receita(restaurante: dict, cnpjs_receita: list) -> dict:
    """
    Para UM restaurante do Google Maps, encontra o melhor CNPJ correspondente
    na base da Receita Federal, comparando endereços.

    Args:
        restaurante: dict com pelo menos 'endereco', 'nome', 'cidade', 'uf'
        cnpjs_receita: lista de dicts da tabela cnpjs_receita (já filtrados por cidade)

    Returns:
        dict com 'cnpj', 'score', 'dados' ou {} se não encontrou match
    """
    endereco_maps = restaurante.get("endereco", "")
    if not endereco_maps:
        return {}

    melhor_match = None
    melhor_score = 0.0

    for cnpj_rec in cnpjs_receita:
        # Montar endereço da Receita
        logradouro = cnpj_rec.get("logradouro", "") or ""
        numero = cnpj_rec.get("numero", "") or ""
        bairro = cnpj_rec.get("bairro", "") or ""

        if not logradouro:
            continue

        endereco_receita = logradouro
        score = calcular_score_endereco(
            endereco_maps, endereco_receita,
            numero_receita=numero, bairro_receita=bairro
        )

        if score > melhor_score:
            melhor_score = score
            melhor_match = cnpj_rec

    # Score mínimo para considerar match
    SCORE_MINIMO = 0.55

    if melhor_score >= SCORE_MINIMO and melhor_match:
        return {
            "cnpj": melhor_match.get("cnpj", ""),
            "score": melhor_score,
            "razao_social": melhor_match.get("razao_social", ""),
            "nome_fantasia": melhor_match.get("nome_fantasia", ""),
            "email": melhor_match.get("email", ""),
            "telefone1": melhor_match.get("telefone1", ""),
            "telefone2": melhor_match.get("telefone2", ""),
            "telefone_proprietario": melhor_match.get("telefone_proprietario", ""),
            "capital_social": melhor_match.get("capital_social", 0),
            "porte": melhor_match.get("porte", ""),
            "natureza_juridica": melhor_match.get("natureza_juridica", ""),
            "data_abertura": melhor_match.get("data_abertura", ""),
            "simples": melhor_match.get("simples", 0),
            "mei": melhor_match.get("mei", 0),
            "socios_json": melhor_match.get("socios_json", "[]"),
            "cnae_principal": melhor_match.get("cnae_principal", ""),
            "logradouro_receita": melhor_match.get("logradouro", ""),
            "numero_receita": melhor_match.get("numero", ""),
            "bairro_receita": melhor_match.get("bairro", ""),
            "tem_ifood_receita": melhor_match.get("tem_ifood", 0),
            "ifood_nome_receita": melhor_match.get("ifood_nome", ""),
            "ifood_url_receita": melhor_match.get("ifood_url", ""),
        }

    return {}


def cruzar_cidade_completa(cidade: str, uf: str) -> dict:
    """
    Cruza TODOS os restaurantes do Google Maps com a base de CNPJs da Receita
    para uma cidade.

    Fluxo:
    1. Carrega todos os restaurantes da cidade (tabela restaurantes)
    2. Carrega todos os CNPJs da Receita da cidade (tabela cnpjs_receita)
    3. Para cada restaurante sem CNPJ, busca match por endereço
    4. Atualiza o restaurante com os dados do CNPJ encontrado

    Returns:
        dict: estatísticas do cruzamento
    """
    conn = _get_connection()

    try:
        # Carregar restaurantes SEM CNPJ
        restaurantes = conn.execute("""
            SELECT id, nome, endereco, cidade, uf
            FROM restaurantes
            WHERE cidade = ? AND uf = ?
            AND (cnpj IS NULL OR cnpj = '')
        """, (cidade, uf)).fetchall()
        restaurantes = [dict(r) for r in restaurantes]

        # Carregar CNPJs da Receita (ativos, da mesma cidade)
        cnpjs = conn.execute("""
            SELECT * FROM cnpjs_receita
            WHERE cidade = ? AND uf = ? AND situacao_cadastral = 'ATIVA'
        """, (cidade.upper(), uf.upper())).fetchall()
        cnpjs = [dict(c) for c in cnpjs]

        if not restaurantes:
            log.info(f"[MATCH] Todos os restaurantes de {cidade}/{uf} já têm CNPJ.")
            return {"total": 0, "matched": 0, "sem_match": 0}

        if not cnpjs:
            log.warning(f"[MATCH] ⚠️ Nenhum CNPJ da Receita para {cidade}/{uf}.")
            log.warning(f"        Execute primeiro: Coleta Base Receita (opção A do menu)")
            return {"total": len(restaurantes), "matched": 0, "sem_match": len(restaurantes)}

        log.info(f"[MATCH] 🔄 Cruzando {len(restaurantes)} restaurantes × {len(cnpjs)} CNPJs...")

        matched = 0
        sem_match = 0
        cnpjs_usados = set()  # Evitar que dois restaurantes usem o mesmo CNPJ

        for i, rest in enumerate(restaurantes):
            # Filtrar CNPJs já usados para evitar duplicatas
            cnpjs_disponiveis = [c for c in cnpjs if c["cnpj"] not in cnpjs_usados]

            resultado = cruzar_restaurante_com_receita(rest, cnpjs_disponiveis)

            if resultado and resultado["cnpj"]:
                # Atualizar restaurante com dados do CNPJ
                import json
                socios_data = resultado.get("socios_json", "[]")

                # Propagar iFood da Receita se o restaurante ainda não tem
                tem_ifood_rec = resultado.get("tem_ifood_receita", 0)
                ifood_nome_rec = resultado.get("ifood_nome_receita", "")
                ifood_url_rec = resultado.get("ifood_url_receita", "")

                conn.execute("""
                    UPDATE restaurantes SET
                        cnpj = ?,
                        razao_social = ?,
                        nome_fantasia = COALESCE(NULLIF(?, ''), nome_fantasia),
                        situacao_cadastral = 'ATIVA',
                        data_abertura = ?,
                        natureza_juridica = ?,
                        capital_social = ?,
                        email_receita = ?,
                        telefones_receita = ?,
                        telefone_proprietario = ?,
                        porte_empresa = ?,
                        simples = ?,
                        mei = ?,
                        score_confianca = ?,
                        tem_ifood = CASE WHEN tem_ifood = 0 THEN ? ELSE tem_ifood END,
                        ifood_nome = CASE WHEN ifood_nome IS NULL OR ifood_nome = '' THEN ? ELSE ifood_nome END,
                        ifood_url = CASE WHEN ifood_url IS NULL OR ifood_url = '' THEN ? ELSE ifood_url END,
                        status = 'enriquecido',
                        data_atualizacao = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    resultado["cnpj"],
                    resultado["razao_social"],
                    resultado["nome_fantasia"],
                    resultado["data_abertura"],
                    resultado["natureza_juridica"],
                    resultado["capital_social"],
                    resultado["email"],
                    "|".join(filter(None, [resultado.get("telefone1", ""), resultado.get("telefone2", "")])),
                    resultado.get("telefone_proprietario", ""),
                    resultado["porte"],
                    resultado["simples"],
                    resultado["mei"],
                    resultado["score"],
                    tem_ifood_rec,
                    ifood_nome_rec,
                    ifood_url_rec,
                    rest["id"],
                ))

                # Inserir sócios
                try:
                    socios = json.loads(socios_data) if socios_data else []
                    for socio in socios:
                        conn.execute("""
                            INSERT OR IGNORE INTO socios
                            (restaurante_id, nome_socio, qualificacao, tipo, cpf_cnpj_socio, data_entrada)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            rest["id"],
                            socio.get("nome", ""),
                            socio.get("qualificacao", ""),
                            socio.get("tipo", ""),
                            socio.get("cpf_cnpj", ""),
                            socio.get("data_entrada", ""),
                        ))
                except (json.JSONDecodeError, TypeError):
                    pass

                # Marcar CNPJ como usado na tabela da Receita
                conn.execute("""
                    UPDATE cnpjs_receita
                    SET matched = 1, restaurante_id = ?, score_match = ?
                    WHERE cnpj = ?
                """, (rest["id"], resultado["score"], resultado["cnpj"]))

                cnpjs_usados.add(resultado["cnpj"])
                matched += 1

                log_score = f"{'🟢' if resultado['score'] >= 0.8 else '🟡'}"
                log.info(f"  {log_score} ({i+1}/{len(restaurantes)}) "
                      f"{rest['nome'][:30]:30s} → {resultado['cnpj']} "
                      f"(score: {resultado['score']:.2f}) "
                      f"| {resultado['razao_social'][:30]}")

            else:
                sem_match += 1
                if (i + 1) % 50 == 0:
                    log.info(f"  ⏳ ({i+1}/{len(restaurantes)}) processados... "
                          f"{matched} matches, {sem_match} sem match")

        conn.commit()

    finally:
        conn.close()

    stats = {
        "total": len(restaurantes),
        "matched": matched,
        "sem_match": sem_match,
        "taxa": f"{(matched/len(restaurantes)*100):.1f}%" if restaurantes else "0%",
    }

    log.info(f"[MATCH] ═══ RESULTADO DO CRUZAMENTO {cidade}/{uf} ═══")
    log.info(f"  Restaurantes processados: {stats['total']}")
    log.info(f"  🟢 Com CNPJ encontrado:   {stats['matched']} ({stats['taxa']})")
    log.info(f"  🔴 Sem match:             {stats['sem_match']}")

    return stats


# ============================================================
# DETECÇÃO DE SÓCIOS MULTI-RESTAURANTE
# ============================================================

def detectar_multi_restaurante(cidade: str, uf: str) -> dict:
    """
    Após o cruzamento, verifica se algum sócio aparece em mais de 1 CNPJ.
    Marca multi_restaurante=1 nos CNPJs correspondentes.

    Returns:
        dict com estatísticas: total_socios_analisados, multi_restaurante
    """
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT cnpj, socios_json FROM cnpjs_receita
            WHERE cidade = ? AND uf = ? AND matched = 1
            AND socios_json IS NOT NULL AND socios_json != '[]'
        """, (cidade.upper(), uf.upper())).fetchall()

        if not rows:
            log.info(f"[MATCH] Nenhum CNPJ matched com sócios para analisar em {cidade}/{uf}")
            return {"total_socios_analisados": 0, "multi_restaurante": 0}

        # Agrupar por nome de sócio
        socio_cnpjs = defaultdict(set)
        for row in rows:
            try:
                socios = json.loads(row["socios_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            for socio in socios:
                nome = socio.get("nome", "").strip().upper()
                if nome and len(nome) >= 5:
                    socio_cnpjs[nome].add(row["cnpj"])

        # Identificar sócios com múltiplos CNPJs
        cnpjs_multi = set()
        multi_count = 0
        for nome, cnpjs in socio_cnpjs.items():
            if len(cnpjs) >= 2:
                multi_count += 1
                cnpjs_multi.update(cnpjs)
                log.info(f"[MATCH] 🌟 Sócio {nome} tem {len(cnpjs)} restaurantes!")

        # Marcar no banco
        if cnpjs_multi:
            for cnpj in cnpjs_multi:
                conn.execute(
                    "UPDATE cnpjs_receita SET multi_restaurante = 1 WHERE cnpj = ?",
                    (cnpj,)
                )
            conn.commit()
            log.info(f"[MATCH] ✅ {len(cnpjs_multi)} CNPJs marcados como multi-restaurante "
                  f"({multi_count} sócios com 2+ restaurantes)")

        return {
            "total_socios_analisados": len(socio_cnpjs),
            "multi_restaurante": multi_count,
            "cnpjs_marcados": len(cnpjs_multi),
        }
    finally:
        conn.close()


# ============================================================
# TESTE DE ENDEREÇOS (para validação)
# ============================================================

def testar_similaridade():
    """Testa o algoritmo de similaridade com exemplos reais."""
    testes = [
        # (Endereço Maps, Logradouro Receita, Número Receita, Bairro, Deve dar match?)
        ("Av. do Batel, 1440 - Batel, Curitiba",
         "AVENIDA DO BATEL", "1440", "BATEL", True),

        ("R. Pres. Carlos Cavalcanti, 1222 - São Francisco, Curitiba",
         "RUA PRESIDENTE CARLOS CAVALCANTI", "1222", "SAO FRANCISCO", True),

        ("Al. Dr. Carlos de Carvalho, 680 - Centro, Curitiba",
         "ALAMEDA DOUTOR CARLOS DE CARVALHO", "680", "CENTRO", True),

        ("Av. do Batel, 1440 - Batel, Curitiba",
         "RUA VOLUNTARIOS DA PATRIA", "555", "CENTRO", False),

        ("R. Buenos Aires, 50 - Centro, Curitiba",
         "RUA BUENOS AIRES", "50", "CENTRO", True),

        ("Av. Sete de Setembro, 4698 - Batel, Curitiba",
         "AVENIDA SETE DE SETEMBRO", "4698", "BATEL", True),

        ("Tv. Nestor de Castro, 55 - Centro, Curitiba",
         "TRAVESSA NESTOR DE CASTRO", "55", "CENTRO", True),

        # Caso difícil: sem número
        ("R. Bom Jesus - Cabral, Curitiba",
         "RUA BOM JESUS", "", "CABRAL", True),

        # Caso difícil: número diferente
        ("Av. do Batel, 1440 - Batel, Curitiba",
         "AVENIDA DO BATEL", "1880", "BATEL", False),
    ]

    print("\n═══ TESTE DE SIMILARIDADE DE ENDEREÇOS ═══\n")

    acertos = 0
    for maps, receita, numero, bairro, esperado in testes:
        score = calcular_score_endereco(maps, receita, numero, bairro)
        match = score >= 0.55
        correto = match == esperado
        acertos += 1 if correto else 0

        emoji = "✅" if correto else "❌"
        print(f"{emoji} Score: {score:.2f} | Match: {'SIM' if match else 'NÃO':3s} "
              f"| Esperado: {'SIM' if esperado else 'NÃO':3s}")
        print(f"   Maps:    {maps}")
        print(f"   Receita: {receita}, {numero} - {bairro}")
        print()

    print(f"Acurácia: {acertos}/{len(testes)} ({acertos/len(testes)*100:.0f}%)")


if __name__ == "__main__":
    testar_similaridade()
