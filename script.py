import os
import imaplib
import email
from email.header import decode_header
import requests
import json
import time
from datetime import datetime
from pypdf import PdfReader
from google import genai

# Aumenta o limite de bytes do IMAP para suportar listas grandes
imaplib._MAXLINE = 10000000

# Credenciais do ambiente (GitHub Secrets)
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
                
                # Verifica palavras-chave no assunto
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
        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
            continue
        
        filename = part.get_filename()
        if filename and filename.lower().endswith('.pdf'):
            print(f"\n📎 Analisando anexo: {filename}")
            pdf_data = part.get_payload(decode=True)
            
            # Salva temporariamente
            with open("temporario.pdf", "wb") as f:
                f.write(pdf_data)
            
            # 1. Extração local de texto via Python (pypdf)
            reader = PdfReader("temporario.pdf")
            texto_pdf = ""
            for page in reader.pages:
                texto_pdf += page.extract_text() or ""
            
            # Checa localmente se existe "CB Rejected"
            if "CB REJECTED" not in texto_pdf.upper():
                print("ℹ️ Nenhuma rejeição ('CB Rejected') identificada no texto do PDF.")
                continue

            print("🚨 ATENÇÃO: Status 'CB Rejected' detectado no PDF! Extraindo nomes com o Gemini...")
            
            # 2. Envia o texto extraído para o Gemini (Economiza cota de API)
            client = genai.Client(api_key=GEMINI_KEY)
            
            prompt = f"""
            Analise o texto a seguir extraído de um relatório financeiro.
            
            Sua missão:
            Identifique todas as transações com status 'CB Rejected' (ou 'CB REJECTED') e extraia apenas o nome do beneficiário/favorecido ("Beneficiary or Debit Party Name").
            
            Texto do relatório:
            {texto_pdf[:15000]}
            
            Resposta esperada:
            Retorne APENAS uma lista simples com os nomes dos beneficiários rejeitados (um por linha). Se não encontrar o nome de nenhum, responda NADA.
            """
            
            sucesso = False
            for tentativa in range(5):
                try:
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=prompt
                    )
                    resultado_ia = response.text.strip()
                    print(f"📋 Resposta da IA:\n{resultado_ia}")
                    
                    if "NADA" not in resultado_ia.upper():
                        enviar_para_slack(assunto_email, resultado_ia)
                    sucesso = True
                    break
                except Exception as e:
                    tempo_espera = 20 + (tentativa * 10)
                    print(f"⚠️ Erro ao consultar Gemini (Tentativa {tentativa + 1}/5): {e}. Aguardando {tempo_espera}s...")
                    time.sleep(tempo_espera)
            
            # Fallback de segurança se a API falhar
            if not sucesso:
                print("❌ Não foi possível obter resposta do Gemini após retentativas. Enviando alerta direto ao Slack...")
                enviar_para_slack(assunto_email, "⚠️ *Atenção:* O status 'CB Rejected' foi encontrado neste anexo, mas a cota da IA excedeu no momento. Favor verificar o comprovante anexado ao e-mail manualmente.")

def enviar_para_slack(titulo_email, nomes_com_erro):
    print("🚨 Disparando notificação no Slack...")
    texto_mensagem = f"⚠️ *Erro de Pagamento Identificado (CB Rejected)!*\n\n*E-mail de Origem:* {titulo_email}\n\n*Detalhes / Beneficiários com Erro:*\n{nomes_com_erro}"
    payload = {"text": texto_mensagem}
    requests.post(SLACK_URL, json=payload)

if __name__ == "__main__":
    buscar_e_processar()
