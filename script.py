import os
import imaplib
import email
from email.header import decode_header
import requests
import json
import re
from datetime import datetime
from pypdf import PdfReader

# Aumenta o limite de bytes do IMAP
imaplib._MAXLINE = 10000000

# Credenciais do ambiente (GitHub Secrets)
EMAIL_USER = os.getenv("EMAIL_USUARIO")
EMAIL_PASS = os.getenv("EMAIL_SENHA")
SLACK_URL = os.getenv("SLACK_WEBHOOK_URL")

PALAVRAS_CHAVE = ["linkedstore", "viver", "mandae"]

def selecionar_caixa_de_emails(mail):
    pastas_possiveis = [
        '"[Gmail]/Todos os e-mails"',
        '"[Gmail]/All Mail"',
        '[Gmail]/Todos os e-mails',
        '[Gmail]/All Mail',
        'INBOX'
    ]
    
    for pasta in pastas_possiveis:
        status, _ = mail.select(pasta)
        if status == 'OK':
            print(f"✅ Pasta selecionada com sucesso: {pasta}")
            return True
            
    print("⚠️ Usando 'INBOX' padrão...")
    mail.select('INBOX')
    return True

def buscar_e_processar():
    print("Conectando ao Gmail...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    
    selecionar_caixa_de_emails(mail)

    # Filtra APENAS e-mails recebidos HOJE
    data_hoje = datetime.now().strftime("%d-%b-%Y")
    criterio_busca = f'(ON "{data_hoje}")'
    
    print(f"Buscando apenas e-mails recebidos hoje ({data_hoje})...")
    status, messages = mail.search(None, criterio_busca)
    
    if status != 'OK' or not messages[0]:
        print("Nenhum e-mail recebido hoje até o momento.")
        mail.logout()
        return

    email_ids = messages[0].split()
    print(f"Total de e-mails recebidos hoje: {len(email_ids)}.")

    for e_id in email_ids:
        res, msg_data = mail.fetch(e_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                raw_subject = msg["Subject"]
                subject = ""
                if raw_subject:
                    headers = decode_header(raw_subject)
                    for text, encoding in headers:
                        if isinstance(text, bytes):
                            subject += text.decode(encoding or "utf-8", errors="ignore")
                        else:
                            subject += str(text)
                
                subject_lower = subject.lower()
                
                if any(p in subject_lower for p in PALAVRAS_CHAVE) and ("comprovante" in subject_lower or "pagamento" in subject_lower):
                    print(f"\n✅ E-MAIL ALVO ENCONTRADO: {subject}")
                    
                    _, full_msg_data = mail.fetch(e_id, "(RFC822)")
                    for full_part in full_msg_data:
                        if isinstance(full_part, tuple):
                            full_msg = email.message_from_bytes(full_part[1])
                            processar_anexos(full_msg, subject)

    mail.logout()
    print("\nProcesso finalizado com sucesso!")

def extrair_rejeicoes_python(texto_pdf):
    rejeicoes = []
    
    blocos = re.split(r'Transaction Initiation Payment Details Report|Discount Rate', texto_pdf)
    
    for bloco in blocos:
        if "CB REJECTED" in bloco.upper():
            match_nome = re.search(r'Beneficiary or Debit Party Name[\s\n]*\|?[\s\n]*([^\n\r|]+)', bloco, re.IGNORECASE)
            match_valor = re.search(r'Payment Currency/Payment Amount[\s\n]*\|?[\s\n]*([^\n\r]+)', bloco, re.IGNORECASE)
            
            nome = match_nome.group(1).strip() if match_nome else None
            valor = match_valor.group(1).strip() if match_valor else None
            
            if not nome:
                linhas = [l.strip() for l in bloco.split('\n') if l.strip()]
                for i, linha in enumerate(linhas):
                    if "Beneficiary or Debit Party Name" in linha and i + 1 < len(linhas):
                        nome = linhas[i + 1].replace('|', '').strip()
                        break
            
            if not valor:
                linhas = [l.strip() for l in bloco.split('\n') if l.strip()]
                for i, linha in enumerate(linhas):
                    if "Payment Currency/Payment Amount" in linha and i + 1 < len(linhas):
                        valor = linhas[i + 1].replace('|', '').strip()
                        break

            nome_final = nome if nome else "Nome não localizado"
            valor_final = valor if valor else "Valor não localizado"
            
            rejeicoes.append(f"• *Favorecido:* `{nome_final}` | *Valor:* `{valor_final}`")
            
    return list(set(rejeicoes))

def processar_anexos(msg, assunto_email):
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
            continue
        
        filename = part.get_filename()
        if filename and filename.lower().endswith('.pdf'):
            print(f"\n📎 Analisando anexo: {filename}")
            pdf_data = part.get_payload(decode=True)
            
            with open("temporario.pdf", "wb") as f:
                f.write(pdf_data)
            
            reader = PdfReader("temporario.pdf")
            texto_pdf = ""
            for page in reader.pages:
                texto_pdf += page.extract_text() or ""
            
            if "CB REJECTED" in texto_pdf.upper():
                print("🚨 Status 'CB Rejected' detectado! Extraindo detalhes via Python...")
                
                lista_erros = extrair_rejeicoes_python(texto_pdf)
                
                if lista_erros:
                    detalhes_formatados = "\n".join(lista_erros)
                else:
                    detalhes_formatados = "• Status 'CB Rejected' encontrado no PDF (verifique o documento em anexo)."
                
                enviar_para_slack(assunto_email, detalhes_formatados)
            else:
                print("ℹ️ Nenhuma rejeição ('CB Rejected') identificada neste PDF.")

def enviar_para_slack(titulo_email, nomes_com_erro):
    print("🚨 Disparando notificação no Slack...")
    
    # Notifica todos os participantes do canal
    texto_mensagem = f"⚠️ <!channel> *Erro de Pagamento Identificado (CB Rejected)!*\n\n*E-mail de Origem:* {titulo_email}\n\n*Detalhes dos Lançamentos Rejeitados:*\n{nomes_com_erro}"
    payload = {"text": texto_mensagem}
    requests.post(SLACK_URL, json=payload)

if __name__ == "__main__":
    buscar_e_processar()
