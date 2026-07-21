import os
import imaplib
import email
from email.header import decode_header
import requests
import json
from google import genai

# Credenciais do ambiente
EMAIL_USER = os.getenv("EMAIL_USUARIO")
EMAIL_PASS = os.getenv("EMAIL_SENHA")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
SLACK_URL = os.getenv("SLACK_WEBHOOK_URL")

PALAVRAS_CHAVE = ["linkedstore", "viver", "mandae"]

def buscar_e_processar():
    print("Conectando ao Gmail...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    
    # IMPORTANTE: Acessa "Todos os e-mails" para pegar mensagens com etiquetas como "Financeiro - NS"
    mail.select('"[Gmail]/All Mail"')
    print("Acessado '[Gmail]/All Mail' com sucesso!")

    status, messages = mail.search(None, "ALL")
    email_ids = messages[0].split()
    
    # Analisa os últimos 100 e-mails
    limite = 100
    ultimos_ids = email_ids[-limite:] if len(email_ids) >= limite else email_ids
    print(f"Verificando os últimos {len(ultimos_ids)} e-mails da conta...")

    for e_id in ultimos_ids:
        # Busca o cabeçalho do e-mail
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
                
                # Checa se é um dos e-mails alvo
                if any(p in subject_lower for p in PALAVRAS_CHAVE) and ("comprovante" in subject_lower or "pagamento" in subject_lower):
                    print(f"\n✅ E-MAIL ENCONTRADO: {subject}")
                    
                    # Baixa a mensagem completa com o anexo
                    _, full_msg_data = mail.fetch(e_id, "(RFC822)")
                    for full_part in full_msg_data:
                        if isinstance(full_part, tuple):
                            full_msg = email.message_from_bytes(full_part[1])
                            processar_anexos(full_msg, subject)

    mail.logout()
    print("\nProcesso finalizado!")

def processar_anexos(msg, assunto_email):
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        if part.get('Content-Disposition') is None:
            continue
        
        filename = part.get_filename()
        if filename and filename.lower().endswith('.pdf'):
            print(f"📎 Processando anexo: {filename}")
            pdf_data = part.get_payload(decode=True)
            
            with open("temporario.pdf", "wb") as f:
                f.write(pdf_data)
            
            print("🤖 Enviando PDF para análise no Gemini...")
            client = genai.Client(api_key=GEMINI_KEY)
            
            documento = client.files.upload(file="temporario.pdf")
            
            prompt = """
            Examine detalhadamente este documento de comprovante/relatório de pagamento.
            
            Sua missão:
            1. Procure por qualquer linha, tabela ou registro que contenha o status de erro ou rejeição 'CB REJECTED' (ou 'CB_REJECTED').
            2. Para cada registro com 'CB REJECTED', identifique e extraia o nome associado no campo 'Beneficiary or debit party name' (ou nome do beneficiário/favorecido).
            
            Formato da resposta:
            - Se encontrar rejeições, retorne APENAS uma lista simples com os nomes dos beneficiários rejeitados (um por linha).
            - Se NÃO houver nenhuma ocorrência de 'CB REJECTED', responda estritamente com a palavra: NADA
            """
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[documento, prompt]
            )
            
            resultado_ia = response.text.strip()
            print(f"📋 Diagnóstico do Gemini:\n{resultado_ia}")
            
            if "NADA" not in resultado_ia.upper():
                enviar_para_slack(assunto_email, resultado_ia)
            else:
                print("Nenhum erro 'CB REJECTED' encontrado no anexo.")

def enviar_para_slack(titulo_email, nomes_com_erro):
    print("🚨 Disparando notificação no Slack...")
    texto_mensagem = f"⚠️ *Erro de Pagamento Identificado (CB REJECTED)!*\n\n*E-mail de Origem:* {titulo_email}\n\n*Beneficiários com Erro:*\n{nomes_com_erro}"
    payload = {"text": texto_mensagem}
    requests.post(SLACK_URL, json=payload)

if __name__ == "__main__":
    buscar_e_processar()
