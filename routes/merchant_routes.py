from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import os
from werkzeug.utils import secure_filename
import re
from config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS

merchant_bp = Blueprint('merchant', __name__, url_prefix='/merchant')


def normalize_company_name(company_name):
    """Konvertiert Firmennamen in Ordnernamen"""
    replacements = {
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue',
        'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue',
        'ß': 'ss'
    }
    for old, new in replacements.items():
        company_name = company_name.replace(old, new)
    
    company_name = re.sub(r'[^a-zA-Z0-9\s]', '', company_name)
    return company_name.strip().replace(' ', '_').lower()
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_company_folder(company_name):
    normalized_name = normalize_company_name(company_name)
    folder_path = os.path.join(UPLOAD_FOLDER, normalized_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    return folder_path


def get_uploaded_files(company_name):
    folder_path = get_company_folder(company_name)
    if os.path.exists(folder_path):
        return os.listdir(folder_path)
    return []


@merchant_bp.route('/login')
def login_page():
    return render_template('merchant/login.html')

@merchant_bp.route('/login', methods=['POST'])
def login():
    company_name = request.form.get('company_name', '').strip()
    
    if not company_name:
        flash('Bitte geben Sie einen Firmennamen ein.')
        return redirect(url_for('merchant.login_page'))
    
    session['company_name'] = company_name
    
    if 'chat_history' not in session:
        session['chat_history'] = []
    
    session['chat_history'].append({
        'sender': 'system',
        'message': f'Willkommen {company_name}! Bitte laden Sie Ihre KYC-Unterlagen hoch.'
    })
    session.modified = True
    
    return redirect(url_for('merchant.dashboard'))


@merchant_bp.route('/dashboard')
def dashboard():
    if 'company_name' not in session:
        return redirect(url_for('merchant.login_page'))
    
    company_name = session['company_name']
    uploaded_files = get_uploaded_files(company_name)
    chat_history = session.get('chat_history', [])
    
    return render_template('merchant/dashboard.html', 
                         company_name=company_name,
                         uploaded_files=uploaded_files,
                         chat_history=chat_history)


@merchant_bp.route('/upload', methods=['POST'])
def upload():
    if 'company_name' not in session:
        return redirect(url_for('merchant.login_page'))
    
    if 'file' not in request.files:
        flash('Keine Datei ausgewählt.')
        return redirect(url_for('merchant.dashboard'))
    file = request.files['file']
    
    if file.filename == '':
        flash('Keine Datei ausgewählt.')
        return redirect(url_for('merchant.dashboard'))
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        company_name = session['company_name']
        company_folder = get_company_folder(company_name)
        file.save(os.path.join(company_folder, filename))
        
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        session['chat_history'].append({
            'sender': 'system',
            'message': 'Danke für das Hochladen des Dokuments. Ich leite es weiter und melde mich mit Ergebnissen.'
        })
        session.modified = True

        flash(f'Dokument "{filename}" erfolgreich hochgeladen.')
    else:
        flash('Dateityp nicht unterstützt. Erlaubt sind: PDF, DOCX, XLSX, CSV, PNG, JPG, TXT')
    
    return redirect(url_for('merchant.dashboard'))


@merchant_bp.route('/chat', methods=['POST'])
def chat():
    if 'company_name' not in session:
        return jsonify({'error': 'Nicht angemeldet'}), 401
    
    message = request.form.get('message', '').strip()
    
    if not message:
        return jsonify({'error': 'Leere Nachricht'}), 400
    
    if 'chat_history' not in session:
        session['chat_history'] = []
    
    session['chat_history'].append({
        'sender': 'user',
        'message': message
    })

    system_response = 'Danke für die Nachricht. Wir prüfen die Unterlagen und melden uns bei Rückfragen.'
    session['chat_history'].append({
        'sender': 'system',
        'message': system_response
    })
    
    session.modified = True
    
    return jsonify({
        'success': True,
        'user_message': message,
        'system_response': system_response
    })


@merchant_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('merchant.login_page'))

