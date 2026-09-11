from collections import Counter, defaultdict
import html
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

# ============================================================
# CONFIGURAÇÕES / SEGURANÇA
# ============================================================

MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

EMAIL_RECIPIENTS = [
    email.strip() for email in EMAIL_TO.split(",") if email.strip()
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
url_tickets = "https://api.movidesk.com/public/v1/tickets"

# ============================================================
# 1. BUSCAR BUGS DE ALTA PRIORIDADE (ACUMULADO DO MÊS)
# ============================================================

params_mes = {
    "token": MOVIDESK_TOKEN,
    "$select": "id,subject,status,justification,category,createdDate,urgency,clients,baseStatus",
    "$expand": "clients($expand=organization)",
    "$filter": f"createdDate ge {primeiro_dia_mes}T00:00:00.00z",
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

    eh_alta_prioridade = any(
        u in urgencia for u in ["alta", "urgente", "high", "urgent"]
    )
    eh_bug = (
        "bug" in categoria
        or "bug" in assunto
        or "erro" in categoria
        or "falha" in categoria
    )

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

params_abertos = {
    "token": MOVIDESK_TOKEN,
    "$select": "id,subject,status,justification,category,createdDate,urgency,clients,baseStatus",
    "$expand": "clients($expand=organization)",
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

    fechado = any(
        p in base_status or p in status_texto
        for p in ["resol", "fech", "cancel", "solved", "closed"]
    )

    if not fechado:
        created_date_raw = t.get("createdDate")
        if created_date_raw:
            try:
                dt_criacao = datetime.fromisoformat(
                    created_date_raw.replace("Z", "").split(".")[0]
                )
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

tickets_em_aberto.sort(key=lambda x: x["dias_aberto"], reverse=True)

# ============================================================
# 3. FUNÇÕES AUXILIARES
# ============================================================


def esc(value):
    return html.escape(str(value)) if value is not None else ""


def format_date(raw_date):
    if not raw_date:
        return "-"
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
# 4. SEPARAÇÃO DOS TICKETS POR JUSTIFICATIVA
# ============================================================

# Agrupando os tickets em aberto por justificativa
tickets_por_justificativa = defaultdict(list)
for t in tickets_em_aberto:
    justificativa = t.get("justification") or "Sem Justificativa"
    tickets_por_justificativa[justificativa].append(t)

# ============================================================
# 5. MONTAGEM E ENVIO DOS E-MAILS SEPARADOS
# ============================================================

styles_common = """
<style>
body { margin: 0; padding: 0; background-color: #f4f6f8; font-family: Arial, sans-serif; color: #202124; }
.wrapper { width: 100%; padding: 30px 0; }
.container { max-width: 1250px; margin: 0 auto; background: #ffffff; border-radius: 14px; overflow: hidden; box-shadow: 0 3px 14px rgba(0,0,0,0.07); }
.header { padding: 28px 32px; border-bottom: 1px solid #e8eaed; background: #3b1443; color: #ffffff; }
.eyebrow { font-size: 12px; font-weight: bold; letter-spacing: 1.2px; color: #d8b4e2; text-transform: uppercase; margin-bottom: 8px; }
.title { margin: 0; font-size: 24px; color: #ffffff; }
.subtitle { margin: 8px 0 0; font-size: 13px; color: #e5e7eb; }
.content { padding: 26px 32px 32px; }
.section-title { font-size: 16px; font-weight: bold; color: #3b1443; margin: 28px 0 12px; border-bottom: 2px solid #f3f4f6; padding-bottom: 6px; }
.table-wrapper { width: 100%; overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 10px; margin-bottom: 24px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; }
th { background: #f8fafc; color: #6b7280; font-size: 11px; font-weight: bold; padding: 12px 10px; border-bottom: 1px solid #e5e7eb; white-space: nowrap; }
td { padding: 12px 10px; border-bottom: 1px solid #f0f1f3; vertical-align: middle; color: #374151; }
.footer { padding: 18px 32px; border-top: 1px solid #e5e7eb; background: #fafafa; font-size: 11px; color: #9ca3af; text-align: center; }
</style>
"""

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)

        # Envia um e-mail individual para cada grupo de justificativa
        for justificativa, lista_tickets in tickets_por_justificativa.items():

            linhas_tabela = ""
            for t in lista_tickets:
                linhas_tabela += f"""
                <tr>
                    <td style="font-weight: bold; color: #4b5563;">#{esc(t.get('id'))}</td>
                    <td><span style="background: #fff6df; color: #a15c00; padding: 3px 6px; border-radius: 4px; font-weight: bold;">{t.get('dias_aberto')} dia(s)</span></td>
                    <td style="font-weight: bold; color: #1f2937;">{esc(t.get('organizacao_nome'))}</td>
                    <td>{esc(t.get('subject'))}</td>
                    <td>{esc(t.get('category') or '-')}</td>
                    <td>{esc(t.get('status'))}</td>
                    <td>{format_date(t.get('createdDate'))}</td>
                </tr>
                """

            html_content = f"""
            <!DOCTYPE html>
            <html lang="pt-BR">
            <head><meta charset="UTF-8">{styles_common}</head>
            <body>
            <div class="wrapper">
            <div class="container">
                <div class="header">
                    <div class="eyebrow">VIDYA CODE • SUPORTE & ENGENHARIA</div>
                    <h1 class="title">Relatório de Tickets - Justificativa: {esc(justificativa)}</h1>
                    <p class="subtitle">Total de tickets nesta categoria: <strong>{len(lista_tickets)}</strong></p>
                </div>
                <div class="content">
                    <div class="section-title">⏳ Tickets em Aberto</div>
                    <div class="table-wrapper">
                        <table>
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>TEMPO EM ABERTO</th>
                                    <th>ORGANIZAÇÃO</th>
                                    <th>ASSUNTO</th>
                                    <th>CATEGORIA</th>
                                    <th>STATUS</th>
                                    <th>ABERTO EM</th>
                                </tr>
                            </thead>
                            <tbody>
                                {linhas_tabela}
                            </tbody>
                        </table>
                    </div>
                </div>
                <div class="footer">Relatório Automático • Movidesk • Vidya Code</div>
            </div>
            </div>
            </body>
            </html>
            """

            msg = MIMEMultipart()
            msg["From"] = EMAIL_USER
            msg["To"] = ", ".join(EMAIL_RECIPIENTS)
            msg[
                "Subject"
            ] = f"Fila de Abertos - Justificativa: {justificativa} ({len(lista_tickets)} tickets)"
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            server.sendmail(EMAIL_USER, EMAIL_RECIPIENTS, msg.as_string())
            print(
                f"E-mail enviado com sucesso para a justificativa: {justificativa}"
            )

except Exception as e:
    print(f"Erro ao enviar e-mails por justificativa: {e}")
