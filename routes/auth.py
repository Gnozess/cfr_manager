from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from models.admin import Admin
from extensions import db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # Si deja connecte, rediriger directement
    if current_user.is_authenticated:
        if current_user.est_membre:
            return redirect(url_for('abonnements.liste'))
        return redirect(url_for('membres.dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        mot_de_passe = request.form.get('mot_de_passe')
        admin = Admin.query.filter_by(email=email, actif=True).first()

        if admin and admin.check_password(mot_de_passe):
            # Activer la session permanente avec expiration 30 min
            session.permanent = True
            login_user(admin, remember=False)
            if admin.est_membre:
                return redirect(url_for('abonnements.liste'))
            return redirect(url_for('membres.dashboard'))
        else:
            flash('Email ou mot de passe incorrect.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/changer-mot-de-passe', methods=['GET', 'POST'])
@login_required
def changer_mot_de_passe():
    if request.method == 'POST':
        ancien = request.form.get('ancien_mot_de_passe')
        nouveau = request.form.get('nouveau_mot_de_passe')
        confirmation = request.form.get('confirmation')

        if not current_user.check_password(ancien):
            flash('Ancien mot de passe incorrect.', 'danger')
            return redirect(url_for('auth.changer_mot_de_passe'))

        if len(nouveau) < 6:
            flash('Le nouveau mot de passe doit contenir au moins 6 caracteres.', 'danger')
            return redirect(url_for('auth.changer_mot_de_passe'))

        if nouveau != confirmation:
            flash('La confirmation ne correspond pas au nouveau mot de passe.', 'danger')
            return redirect(url_for('auth.changer_mot_de_passe'))

        current_user.set_password(nouveau)
        db.session.commit()
        flash('Mot de passe modifie avec succes !', 'success')

        # Rediriger selon le profil
        if current_user.est_membre:
            return redirect(url_for('abonnements.liste'))
        return redirect(url_for('membres.dashboard'))

    return render_template('auth/changer_mot_de_passe.html')