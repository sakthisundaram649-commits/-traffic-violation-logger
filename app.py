import os
from functools import wraps
from datetime import datetime, date
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_file
)
from flask_sqlalchemy import SQLAlchemy
import qrcode

# ---------------------------------------------------------
# App & Database Configuration
# ---------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-secret-key'  # used to sign session cookies
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///traffic_violations.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Hardcoded admin login (fine for a mini-project / demo, not for real production)
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'


# ---------------------------------------------------------
# Database Model  (this is the "CRUD" table)
# ---------------------------------------------------------
class Violation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(db.String(20), nullable=False)
    violation_type = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(150), nullable=False)
    violation_date = db.Column(db.Date, nullable=False)
    fine_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(10), nullable=False, default='Unpaid')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------
# Simple login-required decorator (this is the "Admin Panel" login gate)
# ---------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------
# Auth routes
# ---------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            flash('Logged in successfully.', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Logged out.', 'info')
    return redirect(url_for('login'))


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------
@app.route('/')
@login_required
def dashboard():
    recent = Violation.query.order_by(Violation.created_at.desc()).limit(5).all()
    total = Violation.query.count()
    unpaid = Violation.query.filter_by(status='Unpaid').count()
    paid = Violation.query.filter_by(status='Paid').count()
    return render_template('dashboard.html', recent=recent, total=total, unpaid=unpaid, paid=paid)


# ---------------------------------------------------------
# Add Violation  (Create)
# ---------------------------------------------------------
@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_violation():
    if request.method == 'POST':
        vehicle_number = request.form.get('vehicle_number', '').strip().upper()
        violation_type = request.form.get('violation_type', '').strip()
        location = request.form.get('location', '').strip()
        violation_date_str = request.form.get('violation_date')
        fine_amount = request.form.get('fine_amount')

        if not all([vehicle_number, violation_type, location, violation_date_str, fine_amount]):
            flash('All fields are required.', 'danger')
            return redirect(url_for('add_violation'))

        try:
            violation_date = datetime.strptime(violation_date_str, '%Y-%m-%d').date()
            fine_amount = float(fine_amount)
        except ValueError:
            flash('Invalid date or fine amount.', 'danger')
            return redirect(url_for('add_violation'))

        record = Violation(
            vehicle_number=vehicle_number,
            violation_type=violation_type,
            location=location,
            violation_date=violation_date,
            fine_amount=fine_amount,
            status='Unpaid'
        )
        db.session.add(record)
        db.session.commit()
        flash('Violation record added successfully.', 'success')
        return redirect(url_for('challan', violation_id=record.id))

    return render_template('add_violation.html', today=date.today().isoformat())


# ---------------------------------------------------------
# View / Search / Filter History  (Read)
# ---------------------------------------------------------
@app.route('/history')
@login_required
def history():
    query = Violation.query

    vehicle_number = request.args.get('vehicle_number', '').strip()
    status = request.args.get('status', '').strip()
    violation_type = request.args.get('violation_type', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    if vehicle_number:
        query = query.filter(Violation.vehicle_number.ilike(f'%{vehicle_number}%'))
    if status:
        query = query.filter_by(status=status)
    if violation_type:
        query = query.filter(Violation.violation_type.ilike(f'%{violation_type}%'))
    if date_from:
        try:
            query = query.filter(Violation.violation_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(Violation.violation_date <= datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    records = query.order_by(Violation.violation_date.desc()).all()
    return render_template('history.html', records=records, filters=request.args)


# ---------------------------------------------------------
# Update status: Unpaid <-> Paid  (Update)
# ---------------------------------------------------------
@app.route('/update_status/<int:violation_id>', methods=['POST'])
@login_required
def update_status(violation_id):
    record = Violation.query.get_or_404(violation_id)
    record.status = 'Paid' if record.status == 'Unpaid' else 'Unpaid'
    db.session.commit()
    flash(f'Status updated to {record.status}.', 'success')
    return redirect(request.referrer or url_for('history'))


# ---------------------------------------------------------
# Digital Challan (receipt) with embedded QR code
# ---------------------------------------------------------
@app.route('/challan/<int:violation_id>')
@login_required
def challan(violation_id):
    record = Violation.query.get_or_404(violation_id)
    return render_template('challan.html', record=record)


# ---------------------------------------------------------
# PUBLIC routes — reached by scanning the QR code, no login needed
# ---------------------------------------------------------
@app.route('/status/<int:violation_id>')
def public_status(violation_id):
    record = Violation.query.get_or_404(violation_id)
    return render_template('status.html', record=record)


@app.route('/qr/<int:violation_id>')
def qr_code(violation_id):
    record = Violation.query.get_or_404(violation_id)
    status_url = url_for('public_status', violation_id=record.id, _external=True)

    qr_img = qrcode.make(status_url)
    buffer = BytesIO()
    qr_img.save(buffer, format='PNG')
    buffer.seek(0)
    return send_file(buffer, mimetype='image/png')


# ---------------------------------------------------------
# Run the app
# ---------------------------------------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0')
