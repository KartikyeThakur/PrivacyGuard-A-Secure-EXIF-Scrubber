import os
import uuid # For generating unique filenames
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from PIL import Image
from PIL.ExifTags import TAGS
from googleapiclient.discovery import build

# --- 1. CONFIGURATION ---

# !!! PASTE YOUR *NEW* SECRET KEYS HERE !!!
API_KEY = "AIzaSyCk5nlmCvQWZMIxySAaOU7X7vtWextLkQM"
CSE_ID = "4250c98a883d24373"

# 2. App & Database Setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-very-long-and-random-secret-key-for-security'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db' # This creates the site.db file
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Page to redirect to if user is not logged in
login_manager.login_message_category = 'info'

# --- 2. DATABASE MODELS (The "Tables") ---

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    images = db.relationship('UserImage', backref='owner', lazy=True) # Link to UserImage

class UserImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(100), nullable=False)
    saved_filename = db.Column(db.String(100), unique=True, nullable=False) # The unique filename
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# --- 3. USER ACCOUNT ROUTES (Login, Register, Logout) ---

@app.route("/")
@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and bcrypt.check_password_hash(user.password, request.form['password']):
            login_user(user, remember=True)
            return redirect(url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', title='Login')

@app.route("/register", methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        hashed_password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        user = User(username=request.form['username'], email=request.form['email'], password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register')

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- 4. MAIN APPLICATION ROUTES (The Features) ---

@app.route("/dashboard", methods=['GET', 'POST'])
@login_required # This line protects the page!
def dashboard():
    if request.method == 'POST':
        if 'image' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
        file = request.files['image']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
            
        if file:
            # 1. ANALYZE METADATA
            img = Image.open(file.stream) # Open file stream
            exif_data_raw = img.getexif()
            metadata = {}

            if exif_data_raw:
                for tag_id, value in exif_data_raw.items():
                    tag = TAGS.get(tag_id, tag_id)
                    # Convert bytes to string if possible
                    if isinstance(value, bytes):
                        try:
                            value = value.decode('utf-8')
                        except UnicodeDecodeError:
                            value = str(value)
                    metadata[str(tag)] = str(value)
            
            # 2. SCRUB METADATA
            img_data = list(img.getdata())
            scrubbed_img = Image.new(img.mode, img.size)
            scrubbed_img.putdata(img_data)
            
            # 3. SAVE THE SCRUBBED IMAGE
            random_hex = uuid.uuid4().hex
            _, f_ext = os.path.splitext(file.filename)
            saved_filename = random_hex + f_ext
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)
            scrubbed_img.save(save_path)
            
            # 4. SAVE RECORD TO DATABASE
            new_image_record = UserImage(
                original_name=file.filename,
                saved_filename=saved_filename,
                user_id=current_user.id
            )
            db.session.add(new_image_record)
            db.session.commit()
            
            # 5. RENDER THE REPORT PAGE
            return render_template('report.html', 
                                   title='Report', 
                                   metadata=metadata, 
                                   image_filename=saved_filename)
            
    return render_template('dashboard.html', title='Dashboard')

@app.route("/download/<string:filename>")
@login_required
def download_image(filename):
    # Security check: Make sure the current user *owns* this file
    img_record = UserImage.query.filter_by(saved_filename=filename, user_id=current_user.id).first()
    if img_record:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    else:
        flash('You do not have permission to access this file.', 'danger')
        return redirect(url_for('vault'))

@app.route("/vault")
@login_required
def vault():
    user_images = UserImage.query.filter_by(user_id=current_user.id).order_by(UserImage.id.desc()).all()
    return render_template('vault.html', title='My Vault', images=user_images)

@app.route("/traceback/<int:image_id>")
@login_required
def traceback(image_id):
    # Security check: Make sure user owns this image
    image = UserImage.query.filter_by(id=image_id, user_id=current_user.id).first_or_404()
    
    image_url = url_for('static', filename='uploads/' + image.saved_filename, _external=True)
    
    try:
        service = build("customsearch", "v1", developerKey=API_KEY)
        res = service.cse().list(
            q=image_url,    # Search query is the URL of our image
            cx=CSE_ID,
            searchType='image'
        ).execute()
        results = res.get('items', []) # 'items' might not exist if no results
        
    except Exception as e:
        flash(f'Error connecting to Google API: {e}', 'danger')
        results = []

    return render_template('traceback_report.html', title='Traceback Report', results=results, image=image)

# --- 5. RUN THE APP ---

if __name__ == '__main__':
    # This block creates the database if it doesn't exist
    with app.app_context():
        db.create_all()
    app.run(debug=True)