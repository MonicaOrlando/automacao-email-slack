import os
import imaplib
import email
from email.header import decode_header
import requests
import json
import time
from datetime import datetime
from google import genai

# Aumenta o limite de bytes do IMAP
imaplib._MAXLINE = 10000000

# Credenciais do ambiente
EMAIL_USER = os.getenv("EMAIL_USUARIO")
EMAIL_PASS = os.getenv("EMAIL_SENHA")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
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

    # Filtra APENAS e-mails recebidos na data de HOJE
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
            
            # Pausa inicial de 10 segundos para dar tempo entre requisições
            time.sleep(10)
            
            print("🤖 Enviando PDF para análise do Gemini...")
            client = genai.Client(api_key=GEMINI_KEY)
            
            documento = client.files.upload(file="temporario.pdf")
            
            prompt = """
            Examine este relatório/comprovante bancário em PDF.
            
            Sua missão:
            1. Procure por qualquer transação cujo "Status" seja "CB Rejected" ou "CB REJECTED".
            2. Para CADA transação com status "CB Rejected", extraia o nome que está no campo "Beneficiary or Debit Party Name".
            
            Instruções estritas de formato:
            - Se encontrar transações com "CB Rejected", retorne apenas a lista com o nome dos beneficiários afetados (um por linha).
            - Se NÃO houver nenhuma ocorrência de "CB Rejected", responda EXATAMENTE com a palavra: NADA
            """
            
            # 5 tentativas com pausa progressiva para contornar o limite de cota
            for tentativa in range(5):
                try:
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[documento, prompt]
                    )
                    resultado_ia = response.text.strip()
                    print(f"📋 Diagnóstico do Gemini:\n{resultado_ia}")
                    
                    if "NADA" not in resultado_ia.upper():
                        enviar_para_slack(assunto_email, resultado_ia)
                    else:
                        print("Nenhum erro 'CB Rejected' encontrado neste anexo.")
                    break
                except Exception as e:
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        tempo_espera = 25 + (tentativa * 10)
                        print(f"⚠️ Limite de requisições atingido. Aguardando {tempo_espera}s para tentar novamente (Tentativa {tentativa + 1}/5)...")
                        time.sleep(tempo_espera)
                    else:
                        print(f"❌ Erro ao consultar o Gemini: {e}")
                        break

def enviar_para_slack(titulo_email, nomes_com_erro):
    print("🚨 Disparando notificação no Slack...")
    texto_mensagem = f"⚠️ *Erro de Pagamento Identificado (CB Rejected)!*\n\n*E-mail de Origem:* {titulo_email}\n\n*Beneficiários com Erro:*\n{nomes_com_erro}"
    payload = {"text": texto_mensagem}
    requests.post(SLACK_URL, json=payload)

if __name__ == "__main__":
    buscar_e_processar()
