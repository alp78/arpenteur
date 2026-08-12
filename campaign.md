# Campaign brief

This file is what the `match` stage scores companies against. It is read
verbatim into the prompt, so it is the single thing that decides whether a fit
score means anything.

It is committed and versioned on purpose. The score is only as good as this
file, and when a run produces surprising results the first question is what
changed here, which a diff answers and a database field would not.

Every score stores a digest of this file. Rewriting it invalidates the scores
taken against the old text rather than leaving old and new sitting together
looking equally current, and the next `match` re-scores exactly what the
rewrite made stale.

It is written in French because that is the language the approach will be
written in, and the advice comes back in whatever language it is given.

Drawn from peringer.ch. Two things the site does not state and that are
inferred here, marked below: the headcount band, and who is a bad fit. Both
are worth correcting by hand, because they move scores more than anything
else in the file.

---

## What we do

Ingénieur informatique indépendant, quinze ans de production en systèmes,
développement, données et IA. Cinq domaines : données et IA, développement sur
mesure, cloud et infrastructure, cybersécurité, support et infogérance.

Le client parle directement à l'ingénieur qui réalise. Pas d'agence, pas
d'intermédiaire, pas de revente de matériel ni de licences : aucune
recommandation ne rapporte de commission. Le code source, la documentation et
les accès appartiennent au client. Les données restent en Suisse quand
l'activité l'exige.

L'entrée en matière est un diagnostic de deux à quatre jours à prix fixe
(CHF 2'400 à 4'800), avant tout engagement plus large. Les projets sont à prix
fixés d'avance, le suivi se fait en forfait mensuel d'heures réservées.

## Who we serve best

*Bande d'effectif inférée, à corriger : le site n'en donne aucune.*

Entre 5 et 80 personnes, sans informaticien interne, avec un dirigeant ou un
responsable accessible qui décide lui-même. L'informatique a grandi par
ajouts successifs et commence à coûter du temps plutôt qu'à en faire gagner.

Les activités où l'on retrouve cela : PME et commerces, artisans, fiduciaires
et cabinets, études d'avocats, écoles privées, laboratoires et groupes de
recherche, associations et ONG, producteurs d'énergie, logistique et
industrie.

Trois situations valent mieux qu'un secteur, et pèsent plus lourd :

- Des chiffres justes qui vivent dans plusieurs outils et qu'il faut
  ressaisir à la main pour obtenir un état de la situation
- Une contrainte de confidentialité ou de conformité qui décide de
  l'architecture : secret professionnel, données personnelles, questionnaire
  cyber d'un assureur, exigences d'un mandant public
- Des collaborateurs qui utilisent déjà des assistants IA publics faute
  d'équivalent interne

## Who we serve badly

*Section inférée de « ce que je ne fais pas » sur le site, à corriger.*

- Les sociétés de services informatiques : ce sont des confrères et des
  concurrents, pas des clients
- Les structures de plus de 200 personnes, qui ont une direction informatique
  et passent par appel d'offres
- Qui cherche un revendeur : du matériel, des licences, une marge sur la
  recommandation. Le modèle est sans revente et c'est un désaccord de fond,
  pas un détail de prix
- Qui a besoin d'une astreinte 24/7 ou d'une équipe de garde. Le service est
  du lundi au vendredi, 8h-18h, et l'engagement est celui d'une personne
- Les indépendants sans employés dont les besoins se règlent avec un
  abonnement grand public
- Une société en liquidation, en sursis ou en difficulté déclarée

## What we look for

- Aucun partenaire informatique nommé sur le site
- Un site vitrine ancien, ou pas de site du tout, alors que l'activité est
  réelle et établie
- Des processus décrits comme manuels : ressaisie, tableurs partagés, papier,
  rapprochements faits à la main
- Plusieurs logiciels métier qui ne se parlent pas : caisse, comptabilité,
  stock, CRM, planning
- Une obligation de conformité ou de secret : santé, finance, assurance,
  juridique, données personnelles, mandat public, recherche sous convention
- Une croissance récente : recrutements, nouveau site, nouvelle raison
  sociale, augmentation de capital, ouverture d'un second établissement
- Une activité qui produit de la donnée en volume : mesures, capteurs,
  logistique, laboratoire, production, indices financiers
- Un dirigeant identifiable et joignable, dans une structure assez petite
  pour qu'il décide lui-même

## What warns us off

- Un partenaire informatique déjà nommé et mis en avant
- Une équipe technique interne visible : DSI, CTO, développeurs, postes
  informatiques ouverts au recrutement
- Une raison sociale ou un but social qui décrit une activité informatique,
  de conseil en systèmes ou d'intégration
- Une structure trop grande pour qu'un ingénieur seul soit une réponse
  crédible
- Un statut de registre autre qu'actif

## Proof we can point to

- Pipelines de données sur Google Cloud pour le calcul d'indices financiers
- Extraction de données par Vertex AI, mise en production
- Pipelines Azure pour un producteur d'énergie solaire, et outils de données
  pour un producteur d'énergie
- Pipelines de bio-informatique pour la recherche
- Connecteurs Salesforce vers Tableau
- Automatisation de la production d'un festival
- Restaurations de sauvegarde testées en présence du client, et dossier de
  sauvegarde et de cyber remis à l'assureur
- Diagnostic de deux à quatre jours à prix fixe, qui donne un état des lieux
  utilisable même si rien ne suit

## How we open a conversation

Une observation concrète et vérifiable tirée du site ou de l'activité de
l'entreprise, une phrase sur un travail comparable déjà réalisé, et une
question ouverte sur la situation. Le ton est celui d'un ingénieur qui écrit à
un dirigeant, pas d'un commercial.

Jamais de rendez-vous imposé, jamais de tarif dans un premier message, jamais
de promesse chiffrée sur un gain. Proposer au plus un premier entretien sans
engagement, en disant que quelques lignes suffisent pour commencer.

Cent cinquante mots au maximum. En français, en vouvoyant.
