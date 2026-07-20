import os
import imaplib
import email
from email.header import decode_header
import requests
import json
from google import genai

# 1. Pegando as chaves escondidas no cofre
EMAIL_USER = os.getenv("EMAIL_USUARIO")
EMAIL_PASS = os.getenv("EMAIL_SENHA")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
SLACK_URL = os.getenv("SLACK_WEBHOOK_URL")

# Assuntos que estamos procurando
ASSUNTOS_ALVO = [
    "Comprovante pagamento Linkedstore D-1",
    "Comprovante pagamento Viver D-1",
    "Comprovante pagamento Mandae D-1"
]

def buscar_e_processar():
    print("Conectando ao Gmail...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")

    # Busca e-mails na caixa de entrada
    status, messages = mail.search(None, "ALL")
    email_ids = messages[0].split()

    # Pega apenas os últimos 15 e-mails para focar nos mais recentes
    for e_id in email_ids[-15:]:
        res, msg_data = mail.fetch(e_id, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                subject, encoding = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(encoding or "utf-8")
                
                # Se o assunto for um dos que queremos
                if any(alvo in subject for alvo in ASSUNTOS_ALVO):
                    print(f"Encontrei o e-mail: {subject}")
                    processar_anexos(msg, subject)

    mail.logout()

def processar_anexos(msg, assunto_email):
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        if part.get('Content-Disposition') is None:
            continue
        
        filename = part.get_filename()
        if filename and filename.endswith('.pdf'):
            pdf_data = part.get_payload(decode=True)
            
            # Salvando o PDF temporariamente para enviar para a IA
            with open("temporario.pdf", "wb") as f:
                f.write(pdf_data)
            
            # Chamando o Gemini para ler o PDF
            print("Pedindo para o Gemini analisar o PDF...")
            client = genai.Client(api_key=GEMINI_KEY)
            
            # Enviando o arquivo para o Gemini
            documento = client.files.upload(file="temporario.pdf")
            
            prompt = """
            Analise este documento de pagamento. Procure por todos os registros que tenham o status 'CB REJECTED'.
            Se encontrar algum, extraia o campo 'Beneficiary or debit party name'.
            Retorne APENAS um texto simples listando os nomes encontrados. Se houver mais de um, liste em linhas separadas.
            Se NÃO encontrar nenhum 'CB REJECTED', responda exatamente com a palavra: NADA
            """
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[documento, prompt]
            )
            
            resultado_ia = response.text.strip()
            
            # Se a IA achou erros, avisa no Slack
            if "NADA" not in resultado_ia:
                enviar_para_slack(assunto_email, resultado_ia)
            else:
                print("Nenhum erro encontrado neste PDF.")

def enviar_para_slack(titulo_email, nomes_com_erro):
    print("Enviando aviso para o Slack...")
    texto_mensagem = f"⚠️ *Erro de Pagamento Identificado!*\n\n*E-mail de Origem:* {titulo_email}\n*Beneficiários com Erro (CB REJECTED):*\n{nomes_com_erro}"
    payload = {"text": texto_mensagem}
    requests.post(SLACK_URL, json=payload)

if __name__ == "__main__":
    buscar_e_processar()
