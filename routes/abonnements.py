from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required
from extensions import db
from models.abonnement import Abonnement
from models.paiement import Paiement
from models.remboursement import Remboursement
from models.remise import Remise
from models.membre import Membre
from models.forfait import Forfait
from models.config import Config
from models.caisse import Caisse
from datetime import date, timedelta
from services.sms import sms_paiement, sms_paiement_journalier, sms_annulation
import qrcode
import io
import base64
from flask import Blueprint, render_template, redirect, url_for, flash, request, Response, jsonify
from flask_login import login_required, current_user


abonnements_bp = Blueprint('abonnements', __name__)

MODES_PAIEMENT = [
    ('especes', 'Especes'),
    ('orange_money', 'OrangeMoney'),
    ('mobi_cash', 'MobiCash'),
    ('sank_money', 'SankMoney'),
    ('telecel_money', 'TelecelMoney'),
]


def enregistrer_paiement_caisse(abonnement, montant, mode, motif):
    operation = Caisse(
        type_operation='entree',
        montant=montant,
        motif=motif,
        mode_paiement=mode,
        abonnement_id=abonnement.id,
        membre_id=abonnement.membre_id
    )
    db.session.add(operation)


@abonnements_bp.route('/abonnements')
@login_required
def liste():
    page = request.args.get('page', 1, type=int)
    recherche = request.args.get('q', '')
    filtre_statut = request.args.get('statut', '')
    filtre_type = request.args.get('type', '')
    filtre_acces = request.args.get('acces', '')
    par_page = 10

    query = Abonnement.query.join(Membre)

    # Si profil membre, filtrer uniquement ses abonnements
    if current_user.est_membre:
        if not current_user.membre_id:
            flash('Votre compte n\'est lie a aucun membre.', 'danger')
            return redirect(url_for('membres.dashboard'))
        query = query.filter(Abonnement.membre_id == current_user.membre_id)
    else:
        if recherche:
            query = query.filter(
                (Membre.nom.ilike(f'%{recherche}%')) |
                (Membre.prenom.ilike(f'%{recherche}%')) |
                (Membre.telephone.ilike(f'%{recherche}%'))
            )

        if filtre_statut == 'actif':
            query = query.filter(
                Abonnement.statut == 'actif',
                Abonnement.date_fin >= date.today()
            )
        elif filtre_statut == 'expire':
            query = query.filter(Abonnement.date_fin < date.today())
        elif filtre_statut == 'annule':
            query = query.filter(Abonnement.statut == 'annule')
        elif filtre_statut == 'bientot':
            query = query.filter(
                Abonnement.statut == 'actif',
                Abonnement.date_fin >= date.today(),
                Abonnement.date_fin <= date.fromordinal(
                    date.today().toordinal() + 7)
            )

        if filtre_type:
            query = query.filter(Abonnement.type_abonnement == filtre_type)

        if filtre_acces:
            query = query.filter(Abonnement.type_acces == filtre_acces)

    abonnements_pagines = query.order_by(
        Abonnement.date_fin.asc()
    ).paginate(page=page, per_page=par_page, error_out=False)

    return render_template('abonnements/liste.html',
        abonnements=abonnements_pagines,
        recherche=recherche,
        filtre_statut=filtre_statut,
        filtre_type=filtre_type,
        filtre_acces=filtre_acces
    )


@abonnements_bp.route('/abonnements/ajouter', methods=['GET', 'POST'])
@abonnements_bp.route('/abonnements/ajouter/<int:membre_id>', methods=['GET', 'POST'])
@login_required
def ajouter(membre_id=None):
    membres = Membre.query.filter_by(actif=True).all()
    forfaits = Forfait.query.filter_by(actif=True).all()
    prix_journalier_simple = Config.get('journalier_simple', 2000)
    prix_journalier_complet = Config.get('journalier_complet', 3000)

    abonnement_en_cours = None
    date_debut_min = date.today().isoformat()
    est_reabonnement = False

    if membre_id:
        abonnement_en_cours = Abonnement.query.filter_by(
            membre_id=membre_id,
            type_abonnement='forfait',
            statut='actif'
        ).filter(Abonnement.date_fin >= date.today()).first()

        if abonnement_en_cours:
            est_reabonnement = True
            date_debut_min = abonnement_en_cours.date_fin.isoformat()

    if request.method == 'POST':
        membre_id = request.form.get('membre_id')
        type_abonnement = request.form.get('type_abonnement')
        date_debut = date.fromisoformat(request.form.get('date_debut'))
        mode_paiement = request.form.get('mode_paiement', 'especes')
        numero_mobile = request.form.get('numero_mobile', '').strip() or None
        reference_transaction = request.form.get('reference_transaction', '').strip() or None

        abonnement_actif = Abonnement.query.filter_by(
            membre_id=membre_id,
            type_abonnement='forfait',
            statut='actif'
        ).filter(Abonnement.date_fin >= date.today()).first()

        if type_abonnement == 'forfait':
            forfait_id = request.form.get('forfait_id')

            if not forfait_id:
                flash('Veuillez selectionner un forfait.', 'danger')
                return redirect(url_for('abonnements.ajouter', membre_id=membre_id))

            forfait = Forfait.query.get(forfait_id)
            if not forfait:
                flash('Forfait introuvable.', 'danger')
                return redirect(url_for('abonnements.ajouter', membre_id=membre_id))

            nombre_mois = int(request.form.get('nombre_mois') or 1)
            montant_brut = forfait.prix_mensuel * nombre_mois
            pourcentage_remise = float(request.form.get('remise') or 0)
            pourcentage_remise = max(0, min(100, pourcentage_remise))
            montant_remise = round(montant_brut * pourcentage_remise / 100, 0)
            montant_total = montant_brut - montant_remise
            moitie = montant_total / 2
            type_acces = forfait.type_acces
            date_fin = date_debut + timedelta(days=30 * nombre_mois)

            if abonnement_actif and date_debut < abonnement_actif.date_fin:
                flash(
                    'Ce membre a deja un abonnement actif jusqu\'au '
                    + abonnement_actif.date_fin.strftime('%d/%m/%Y')
                    + '. Le reabonnement doit commencer a partir de cette date.',
                    'danger'
                )
                return redirect(url_for('abonnements.ajouter', membre_id=membre_id))

            premier_paiement = request.form.get('premier_paiement')
            if not premier_paiement or float(premier_paiement) < moitie:
                flash(
                    f'Le premier paiement doit etre superieur ou egal a la moitie '
                    f'du montant total ({moitie:,.0f} FCFA).',
                    'danger'
                )
                return redirect(url_for('abonnements.ajouter', membre_id=membre_id))

            if float(premier_paiement) > montant_total:
                flash(
                    f'Le premier paiement ne peut pas depasser le montant total '
                    f'({montant_total:,.0f} FCFA).',
                    'danger'
                )
                return redirect(url_for('abonnements.ajouter', membre_id=membre_id))

            abonnement = Abonnement(
                membre_id=membre_id,
                forfait_id=forfait_id,
                type_abonnement='forfait',
                type_acces=type_acces,
                nombre_mois=nombre_mois,
                prix_mensuel=forfait.prix_mensuel,
                montant_brut=montant_brut,
                montant_total=montant_total,
                date_debut=date_debut,
                date_fin=date_fin,
                statut='actif'
            )
            db.session.add(abonnement)
            db.session.flush()

            if pourcentage_remise > 0:
                motif_remise = request.form.get('motif_remise') or 'Remise accordee'
                remise = Remise(
                    abonnement_id=abonnement.id,
                    pourcentage=pourcentage_remise,
                    montant_remise=montant_remise,
                    motif=motif_remise
                )
                db.session.add(remise)

            montant_premier = float(premier_paiement)
            paiement = Paiement(
                abonnement_id=abonnement.id,
                montant=montant_premier,
                mode_paiement=mode_paiement,
                numero_mobile=numero_mobile,
                reference_transaction=reference_transaction,
                note='Premier paiement'
            )
            db.session.add(paiement)

            membre_obj = Membre.query.get(membre_id)
            enregistrer_paiement_caisse(
                abonnement, montant_premier, mode_paiement,
                f'Abonnement {forfait.nom} - {membre_obj.prenom} {membre_obj.nom}'
            )

            db.session.commit()

            # SMS confirmation paiement forfait
            try:
                sms_paiement(
                    membre_obj,
                    montant_premier,
                    forfait.nom,
                    abonnement.reste_a_payer
                )
            except Exception as e:
                print(f'Erreur SMS forfait : {e}')

        else:
            if abonnement_actif:
                flash(
                    'Ce membre a deja un abonnement forfait actif. '
                    'Il ne peut pas souscrire un abonnement journalier '
                    'durant sa periode d abonnement.',
                    'danger'
                )
                return redirect(url_for('abonnements.ajouter'))

            type_acces = request.form.get('type_acces_journalier')
            montant_total = prix_journalier_complet if type_acces == 'salle+fitness' \
                else prix_journalier_simple
            date_fin = date_debut

            abonnement = Abonnement(
                membre_id=membre_id,
                forfait_id=None,
                type_abonnement='journalier',
                type_acces=type_acces,
                nombre_mois=None,
                prix_mensuel=None,
                montant_brut=montant_total,
                montant_total=montant_total,
                date_debut=date_debut,
                date_fin=date_fin,
                statut='actif'
            )
            db.session.add(abonnement)
            db.session.flush()

            paiement = Paiement(
                abonnement_id=abonnement.id,
                montant=montant_total,
                mode_paiement=mode_paiement,
                numero_mobile=numero_mobile,
                reference_transaction=reference_transaction,
                note='Paiement journalier'
            )
            db.session.add(paiement)

            membre_obj = Membre.query.get(membre_id)
            enregistrer_paiement_caisse(
                abonnement, montant_total, mode_paiement,
                f'Abonnement journalier - {membre_obj.prenom} {membre_obj.nom}'
            )

            db.session.commit()

            # SMS confirmation paiement journalier
            try:
                sms_paiement_journalier(
                    membre_obj,
                    montant_total,
                    type_acces
                )
            except Exception as e:
                print(f'Erreur SMS journalier : {e}')

        membre = Membre.query.get(membre_id)
        flash(f'Abonnement ajoute pour {membre.prenom} {membre.nom} !', 'success')
        return redirect(url_for('abonnements.liste'))

    membre_selectionne = Membre.query.get(membre_id) if membre_id else None
    return render_template('abonnements/ajouter.html',
        membres=membres,
        forfaits=forfaits,
        membre_selectionne=membre_selectionne,
        prix_journalier_simple=prix_journalier_simple,
        prix_journalier_complet=prix_journalier_complet,
        aujourd_hui=date.today().isoformat(),
        date_debut_min=date_debut_min,
        est_reabonnement=est_reabonnement,
        abonnement_en_cours=abonnement_en_cours,
        modes_paiement=MODES_PAIEMENT
    )

def generer_qr_code(abonnement):
    data = (
        f"CF-RAGUILIO\n"
        f"N: {abonnement.id:05d}\n"
        f"Membre: {abonnement.membre.prenom} {abonnement.membre.nom}\n"
        f"Forfait: {abonnement.forfait.nom if abonnement.forfait else 'Journalier'}\n"
        f"Acces: {abonnement.type_acces}\n"
        f"Debut: {abonnement.date_debut.strftime('%d/%m/%Y')}\n"
        f"Fin: {abonnement.date_fin.strftime('%d/%m/%Y')}"
    )
    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode()

@abonnements_bp.route('/abonnements/recherche-membres')
@login_required
def recherche_membres():
    q = request.args.get('q', '')
    membres = Membre.query.filter(
        Membre.actif == True,
        (Membre.nom.ilike(f'%{q}%')) |
        (Membre.prenom.ilike(f'%{q}%')) |
        (Membre.telephone.ilike(f'%{q}%'))
    ).limit(10).all()
    return jsonify([{
        'id': m.id,
        'nom': m.nom,
        'prenom': m.prenom,
        'telephone': m.telephone or ''
    } for m in membres])


@abonnements_bp.route('/abonnements/detail/<int:id>')
@login_required
def detail(id):
    abonnement = Abonnement.query.get_or_404(id)

    if current_user.est_membre and abonnement.membre_id != current_user.membre_id:
        flash('Acces non autorise.', 'danger')
        return redirect(url_for('abonnements.liste'))

    # Afficher la carte dans tous les cas si paiement existe
    qr_code = None
    if abonnement.montant_paye > 0 or abonnement.est_solde:
        qr_code = generer_qr_code(abonnement)

    return render_template('abonnements/detail.html',
        abonnement=abonnement,
        modes_paiement=MODES_PAIEMENT,
        qr_code=qr_code
    )

@abonnements_bp.route('/abonnements/paiement/<int:id>', methods=['POST'])
@login_required
def ajouter_paiement(id):
    abonnement = Abonnement.query.get_or_404(id)
    montant = float(request.form.get('montant'))
    mode_paiement = request.form.get('mode_paiement', 'especes')
    numero_mobile = request.form.get('numero_mobile', '').strip() or None
    reference_transaction = request.form.get('reference_transaction', '').strip() or None

    if montant <= 0:
        flash('Le montant doit etre superieur a 0.', 'danger')
        return redirect(url_for('abonnements.detail', id=id))

    if montant > abonnement.reste_a_payer:
        reste = f"{abonnement.reste_a_payer:,.0f}"
        flash(f'Le montant depasse le reste a payer ({reste} FCFA).', 'danger')
        return redirect(url_for('abonnements.detail', id=id))

    paiement = Paiement(
        abonnement_id=id,
        montant=montant,
        mode_paiement=mode_paiement,
        numero_mobile=numero_mobile,
        reference_transaction=reference_transaction,
        note=request.form.get('note', '')
    )
    db.session.add(paiement)

    membre = abonnement.membre
    enregistrer_paiement_caisse(
        abonnement, montant, mode_paiement,
        f'Paiement tranche - {membre.prenom} {membre.nom}'
    )

    db.session.commit()

    # SMS confirmation tranche de paiement
    try:
        sms_paiement(
            abonnement.membre,
            montant,
            abonnement.forfait.nom if abonnement.forfait else 'Forfait',
            abonnement.reste_a_payer
        )
    except Exception as e:
        print(f'Erreur SMS tranche : {e}')

    flash(f'Paiement de {montant:,.0f} FCFA enregistre !', 'success')
    return redirect(url_for('abonnements.detail', id=id))


@abonnements_bp.route('/abonnements/annuler/<int:id>', methods=['POST'])
@login_required
def annuler(id):
    abonnement = Abonnement.query.get_or_404(id)

    if abonnement.type_abonnement != 'forfait':
        flash('Seul un abonnement forfait peut etre annule.', 'danger')
        return redirect(url_for('abonnements.detail', id=id))

    aujourd_hui = date.today()
    montant_rembourse = abonnement.montant_paye
    membre = abonnement.membre

    abonnement.statut = 'annule'
    abonnement.date_fin = aujourd_hui
    abonnement.date_annulation = aujourd_hui

    remboursement = Remboursement(
        abonnement_id=abonnement.id,
        montant_rembourse=montant_rembourse,
        motif='Annulation - restitution des paiements percus'
    )
    db.session.add(remboursement)

    paiements = abonnement.paiements
    modes_totaux = {}
    for p in paiements:
        modes_totaux[p.mode_paiement] = modes_totaux.get(p.mode_paiement, 0) + p.montant

    for mode, montant in modes_totaux.items():
        sortie = Caisse(
            type_operation='sortie',
            montant=montant,
            motif=f'Remboursement annulation ({Caisse.MODES.get(mode, mode)}) '
                  f'- {membre.prenom} {membre.nom}',
            mode_paiement=mode,
            abonnement_id=abonnement.id,
            membre_id=membre.id
        )
        db.session.add(sortie)

    db.session.commit()

    # SMS notification annulation
    try:
        forfait_nom = abonnement.forfait.nom if abonnement.forfait else 'Forfait'
        sms_annulation(membre, forfait_nom, montant_rembourse)
    except Exception as e:
        print(f'Erreur SMS annulation : {e}')

    if montant_rembourse > 0:
        flash(
            f'Abonnement annule. Remboursement total de '
            f'{montant_rembourse:,.0f} FCFA deduit de la caisse '
            f'par mode de paiement.',
            'warning'
        )
    else:
        flash('Abonnement annule. Aucun remboursement effectue.', 'warning')

    return redirect(url_for('abonnements.liste'))

@abonnements_bp.route('/controle-acces')
@login_required
def controle_acces():
    return render_template('abonnements/controle_acces.html')


@abonnements_bp.route('/controle-acces/verifier', methods=['POST'])
@login_required
def verifier_acces():
    data_qr = request.form.get('qr_data', '').strip()

    if not data_qr:
        return jsonify({'statut': 'erreur', 'message': 'QR code vide'})

    # Extraire l'ID abonnement du QR code
    # Format : "CF-RAGUILIO\nN: 00001\n..."
    try:
        lignes = data_qr.split('\n')
        id_ligne = [l for l in lignes if l.startswith('N:')]
        if not id_ligne:
            return jsonify({'statut': 'erreur', 'message': 'QR code invalide'})

        abonnement_id = int(id_ligne[0].replace('N:', '').strip())
        abonnement = Abonnement.query.get(abonnement_id)

        if not abonnement:
            return jsonify({
                'statut': 'refuse',
                'message': 'Abonnement introuvable',
                'couleur': 'danger'
            })

        membre = abonnement.membre

        if abonnement.statut == 'annule':
            return jsonify({
                'statut': 'refuse',
                'message': f'{membre.prenom} {membre.nom} - Abonnement annule',
                'couleur': 'danger'
            })

        if abonnement.est_expire:
            return jsonify({
                'statut': 'refuse',
                'message': f'{membre.prenom} {membre.nom} - Abonnement expire le {abonnement.date_fin.strftime("%d/%m/%Y")}',
                'couleur': 'danger'
            })

        if not abonnement.est_solde:
            return jsonify({
                'statut': 'attention',
                'message': f'{membre.prenom} {membre.nom} - Reste a payer : {abonnement.reste_a_payer:,.0f} FCFA',
                'couleur': 'warning'
            })

        return jsonify({
            'statut': 'autorise',
            'message': f'Bienvenue {membre.prenom} {membre.nom} !',
            'details': f'{abonnement.forfait.nom if abonnement.forfait else "Journalier"} - Valide jusqu\'au {abonnement.date_fin.strftime("%d/%m/%Y")}',
            'couleur': 'success',
            'jours_restants': abonnement.jours_restants
        })

    except Exception as e:
        return jsonify({
            'statut': 'erreur',
            'message': f'Erreur : {str(e)}',
            'couleur': 'danger'
        })
