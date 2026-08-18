import os
import html
import requests
import sys
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import Counter

# ============================================================
# CONFIGURAÇÕES / SEGURANÇA
# ============================================================

MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

EMAIL_RECIPIENTS = [
    email.strip()
    for email in EMAIL_TO.split(",")
    if email.strip()
]

# ============================================================
# VALIDAÇÃO DAS CONFIGURAÇÕES
# ============================================================

required_variables = {
    "MOVIDESK_TOKEN": MOVIDESK_TOKEN,
    "EMAIL_USER": EMAIL_USER,
    "EMAIL_PASSWORD": EMAIL_PASSWORD,
    "EMAIL_TO": EMAIL_TO,
}

missing_variables = [
    name for name, value in required_variables.items() if not value
]

if missing_variables:
    raise RuntimeError(
        "Variáveis de ambiente não configuradas: " + ", ".join(missing_variables)
    )

if not EMAIL_RECIPIENTS:
    raise RuntimeError("EMAIL_TO não possui nenhum destinatário válido.")

hoje = datetime.now()
primeiro_dia_mes = hoje.strftime("%Y-%m-01")
periodo_exibicao = f"Acumulado do Mês ({01}/{hoje.strftime('%m/%Y')} a {hoje.strftime('%d/%m/%Y')}) + Passivo Geral de Abertos"

url_tickets = "https://api.movidesk.com/public/v1/tickets"

# ============================================================
# 1. BUSCAR BUGS DE ALTA PRIORIDADE (ACUMULADO DO MÊS)
# ============================================================

params_mes = {
    "token": MOVIDESK_TOKEN,
    "$select": "id,subject,status,category,createdDate,urgency,clients,baseStatus",
    "$expand": "clients($expand=organization)",
    "$filter": f"createdDate ge {primeiro_dia_mes}T00:00:00.00z"
}

try:
    response_mes = requests.get(url_tickets, params=params_mes, timeout=30)
    response_mes.raise_for_status()
    tickets_mes = response_mes.json()
except Exception as e:
    print(f"Erro ao buscar tickets do mês: {e}")
    tickets_mes = []

if not isinstance(tickets_mes, list):
    tickets_mes = []

bugs_alta_prioridade = []
clientes_bugs_contador = Counter()

for t in tickets_mes:
    urgencia = (t.get("urgency") or "").lower().strip()
    categoria = (t.get("category") or "").lower()
    assunto = (t.get("subject") or "").lower()
    
    eh_alta_prioridade = any(u in urgencia for u in ["alta", "urgente", "high", "urgent"])
    eh_bug = "bug" in categoria or "bug" in assunto or "erro" in categoria or "falha" in categoria
    
    if eh_alta_prioridade and eh_bug:
        organizacao = "Sem Organização"
        for c in t.get("clients", []):
            org = c.get("organization")
            if isinstance(org, dict):
                nome_org = org.get("businessName") or org.get("name")
                if nome_org:
                    organizacao = nome_org
                    break
        t["organizacao_nome"] = organizacao
        bugs_alta_prioridade.append(t)
        clientes_bugs_contador[organizacao] += 1

ranking_clientes_bugs = clientes_bugs_contador.most_common()

# ============================================================
# 2. BUSCAR TODOS OS TICKETS EM ABERTO (INDEPENDENTE DA DATA)
# ============================================================
# Sem filtro de data no $filter para capturar todo o passivo em aberto

params_abertos = {
    "token": MOVIDESK_TOKEN,
    "$select": "id,subject,status,category,createdDate,urgency,clients,baseStatus",
    "$expand": "clients($expand=organization)"
}

try:
    response_abertos = requests.get(url_tickets, params=params_abertos, timeout=30)
    response_abertos.raise_for_status()
    todos_sistema = response_abertos.json()
except Exception as e:
    print(f"Erro ao buscar todos os tickets do sistema: {e}")
    todos_sistema = []

if not isinstance(todos_sistema, list):
    todos_sistema = []

tickets_em_aberto = []
for t in todos_sistema:
    base_status = (t.get("baseStatus") or "").lower()
    status_texto = (t.get("status") or "").lower()
    
    # Exclui fechados, resolvidos ou cancelados
    fechado = any(p in base_status or p in status_texto for p in ["resol", "fech", "cancel", "solved", "closed"])
    
    if not fechado:
        created_date_raw = t.get("createdDate")
        if created_date_raw:
            try:
                dt_criacao = datetime.fromisoformat(created_date_raw.replace("Z", "").split(".")[0])
                delta = hoje - dt_criacao
                t["dias_aberto"] = delta.days
            except:
                t["dias_aberto"] = 0
        else:
            t["dias_aberto"] = 0
            
        organizacao = "Sem Organização"
        for c in t.get("clients", []):
            org = c.get("organization")
            if isinstance(org, dict):
                nome_org = org.get("businessName") or org.get("name")
                if nome_org:
                    organizacao = nome_org
                    break
        t["organizacao_nome"] = organizacao
        tickets_em_aberto.append(t)

# Ordena do que está há mais tempo aberto para o que está há menos tempo
tickets_em_aberto.sort(key=lambda x: x["dias_aberto"], reverse=True)

# ============================================================
# 3. FUNÇÕES AUXILIARES
# ============================================================

def esc(value): return html.escape(str(value)) if value is not None else ""

def format_date(raw_date):
    if not raw_date: return "-"
    try:
        dt_obj = datetime.fromisoformat(raw_date.replace("Z", "").split(".")[0])
        return dt_obj.strftime("%d/%m/%Y %H:%M")
    except:
        return raw_date

def urgency_badge(urgency):
    val = (urgency or "").lower().strip()
    if "urgent" in val or "urgente" in val:
        return '<span class="urgency-urgent">URGENTE</span>'
    elif "alt" in val:
        return '<span class="urgency-high">ALTA</span>'
    return f'<span class="urgency-normal">{esc(urgency or "Normal")}</span>'

# ============================================================
# 4. MONTAGEM DO HTML E E-MAIL
# ============================================================

html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Relatório de Bugs Críticos e Abertos - Vidya Code</title>
<style>
body {{ margin: 0; padding: 0; background-color: #f4f6f8; font-family: Arial, sans-serif; color: #202124; }}
.wrapper {{ width: 100%; padding: 30px 0; }}
.container {{ max-width: 1100px; margin: 0 auto; background: #ffffff; border-radius: 14px; overflow: hidden; box-shadow: 0 3px 14px rgba(0,0,0,0.07); }}
.header {{ padding: 28px 32px; border-bottom: 1px solid #e8eaed; background: #3b1443; color: #ffffff; }}
.eyebrow {{ font-size: 12px; font-weight: bold; letter-spacing: 1.2px; color: #d8b4e2; text-transform: uppercase; margin-bottom: 8px; }}
.title {{ margin: 0; font-size: 24px; color: #ffffff; }}
.subtitle {{ margin: 8px 0 0; font-size: 13px; color: #e5e7eb; }}
.content {{ padding: 26px 32px 32px; }}
.cards {{ display: flex; flex-wrap: wrap; gap: 20px; margin-bottom: 28px; }}
.card {{ flex: 1; min-width: 220px; padding: 20px; background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 10px; box-sizing: border-box; }}
.card-label {{ font-size: 12px; color: #6b7280; margin-bottom: 8px; font-weight: bold; }}
.card-value {{ font-size: 24px; font-weight: bold; color: #111827; }}
.section-title {{ font-size: 16px; font-weight: bold; color: #3b1443; margin: 28px 0 12px; border-bottom: 2px solid #f3f4f6; padding-bottom: 6px; }}
.table-wrapper {{ width: 100%; overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 10px; margin-bottom: 24px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; }}
th {{ background: #f8fafc; color: #6b7280; font-size: 11px; font-weight: bold; padding: 12px 10px; border-bottom: 1px solid #e5e7eb; white-space: nowrap; }}
td {{ padding: 12px 10px; border-bottom: 1px solid #f0f1f3; vertical-align: middle; color: #374151; }}
.urgency-urgent {{ background: #111827; color: #ffffff; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
.urgency-high {{ background: #fdecec; color: #b42318; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
.grid-2 {{ display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 24px; }}
.box-panel {{ flex: 1; min-width: 280px; background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; }}
.box-panel h3 {{ margin-top: 0; font-size: 14px; color: #3b1443; border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
ul {{ margin: 0; padding-left: 18px; font-size: 12px; color: #374151; }}
ul li {{ margin-bottom: 6px; }}
.footer {{ padding: 18px 32px; border-top: 1px solid #e5e7eb; background: #fafafa; font-size: 11px; color: #9ca3af; text-align: center; }}
</style>
</head>
<body>
<div class="wrapper">
<div class="container">
<div class="header">
    <div class="eyebrow">VIDYA CODE • SUPORTE & ENGENHARIA</div>
    <h1 class="title">Relatório de Bugs Críticos e Passivo de Tickets Abertos</h1>
    <p class="subtitle">Bugs avaliados no mês • Tickets em aberto considerando <strong>todo o histórico</strong></p>
</div>
<div class="content">

<div class="cards">
    <div class="card">
        <div class="card-label">BUGS DE ALTA / URGENTE (MÊS)</div>
        <div class="card-value" style="color: #b42318;">{len(bugs_alta_prioridade)}</div>
        <div class="card-detail">No acumulado do mês atual</div>
    </div>
    <div class="card">
        <div class="card-label">TOTAL DE TICKETS EM ABERTO</div>
        <div class="card-value" style="color: #2457a6;">{len(tickets_em_aberto)}</div>
        <div class="card-detail">Passivo geral em atendimento</div>
    </div>
</div>

<div class="grid-2">
    <div class="box-panel">
        <h3>Ranking de Clientes (Bugs de Alta Prioridade - Mês)</h3>
        {
            "<ul>" + "".join([f"<li><b>{esc(org)}</b>: {qtd} bug(s) crítico(s)</li>" for org, qtd in ranking_clientes_bugs]) + "</ul>"
            if ranking_clientes_bugs else "<p style='font-size:12px; color:#18794e;'>Nenhum bug de alta prioridade registrado no mês.</p>"
        }
    </div>
</div>

<div class="section-title">🚨 Relação de Bugs de Alta Prioridade por Cliente (Mês)</div>
<div class="table-wrapper">
<table>
<thead>
<tr>
    <th>ID</th>
    <th>URGÊNCIA</th>
    <th>ORGANIZAÇÃO</th>
    <th>ASSUNTO</th>
    <th>CATEGORIA</th>
    <th>ABERTO EM</th>
</tr>
</thead>
<tbody>
"""

if not bugs_alta_prioridade:
    html_content += """
<tr>
    <td colspan="6" style="text-align: center; padding: 25px; color: #9ca3af;">
        Nenhum bug de alta prioridade ou urgente encontrado no período do mês.
    </td>
</tr>
"""
else:
    for t in bugs_alta_prioridade:
        html_content += f"""
<tr>
    <td style="font-weight: bold; color: #4b5563;">#{esc(t.get('id'))}</td>
    <td>{urgency_badge(t.get('urgency'))}</td>
    <td style="font-weight: bold; color: #1f2937;">{esc(t.get('organizacao_nome'))}</td>
    <td>{esc(t.get('subject'))}</td>
    <td>{esc(t.get('category'))}</td>
    <td>{format_date(t.get('createdDate'))}</td>
</tr>
"""

html_content += f"""
</tbody>
</table>
</div>

<div class="section-title">⏳ Todos os Tickets em Aberto (Ordenados do Mais Antigo para o Mais Recente)</div>
<div class="table-wrapper">
<table>
<thead>
<tr>
    <th>ID</th>
    <th>TEMPO EM ABERTO</th>
    <th>ORGANIZAÇÃO</th>
    <th>ASSUNTO</th>
    <th>STATUS</th>
    <th>ABERTO EM</th>
</tr>
</thead>
<tbody>
"""

if not tickets_em_aberto:
    html_content += """
<tr>
    <td colspan="6" style="text-align: center; padding: 25px; color: #9ca3af;">
        Nenhum ticket em aberto no momento.
    </td>
</tr>
"""
else:
    for t in tickets_em_aberto:
        html_content += f"""
<tr>
    <td style="font-weight: bold; color: #4b5563;">#{esc(t.get('id'))}</td>
    <td><span style="background: #fff6df; color: #a15c00; padding: 3px 6px; border-radius: 4px; font-weight: bold;">{t.get('dias_aberto')} dia(s)</span></td>
    <td style="font-weight: bold; color: #1f2937;">{esc(t.get('organizacao_nome'))}</td>
    <td>{esc(t.get('subject'))}</td>
    <td>{esc(t.get('status'))}</td>
    <td>{format_date(t.get('createdDate'))}</td>
</tr>
"""

html_content += """
</tbody>
</table>
</div>

</div>
<div class="footer">
    Relatório Automático • Movidesk • Vidya Code
</div>
</div>
</div>
</body>
</html>
"""

# ============================================================
# 5. ENVIO DO E-MAIL
# ============================================================

msg = MIMEMultipart()
msg["From"] = EMAIL_USER
msg["To"] = ", ".join(EMAIL_RECIPIENTS)
msg["Subject"] = f"Relatório Consolidado: Bugs Críticos do Mês e Fila Geral de Abertos"

msg.attach(MIMEText(html_content, "html", "utf-8"))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_RECIPIENTS, msg.as_string())
    print("E-mail consolidado enviado com sucesso!")
except Exception as e:
    print(f"Erro ao enviar e-mail: {e}")
