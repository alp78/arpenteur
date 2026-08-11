# Campaign brief

This file is what the `match` stage scores companies against. It is read
verbatim into the prompt, so it is the single thing that decides whether a fit
score means anything.

It is committed and versioned on purpose. The score is only as good as this
file, and when a run produces surprising results the first question is what
changed here, which a diff answers and a database field would not.

**Replace everything below.** What is here is an example, filled in for a
small IT consultancy, so that the stage can be read and tested before the real
brief exists. A brief that has not been written for the campaign at hand will
produce confident scores built on nothing, which is worse than no scores.

Write it in the language you will write to companies in. The model reasons in
whatever it is given, and the approach advice comes back in the same language.

---

## What we do

Nous accompagnons les PME romandes sur leur informatique : infrastructure,
sécurité, automatisation des processus et migration vers le cloud. Nous
travaillons en mandat, pas en régie, et nous restons l'interlocuteur unique.

## Who we serve best

Entre 10 et 80 personnes, un seul décideur accessible, une informatique qui a
grandi sans plan et commence à coûter du temps. Souvent une fiduciaire, un
cabinet, un bureau d'ingénieurs ou une PME industrielle.

## Who we serve badly

Les indépendants sans employés, dont les besoins se règlent avec un abonnement
grand public. Les entreprises de plus de 200 personnes, qui ont déjà une
direction informatique et un appel d'offres. Les sociétés informatiques
elles-mêmes, qui sont des concurrents et non des clients.

## What we look for

- Un site vitrine ancien, ou pas de site du tout
- Des processus décrits comme manuels, du papier, des tableurs partagés
- Une croissance récente : nouveaux sites, recrutements, nouvelle raison sociale
- Des obligations de conformité ou de traitement de données personnelles
- Aucun partenaire informatique nommé

## What warns us off

- Un partenaire informatique déjà nommé et mis en avant
- Une équipe technique interne visible
- Une société en liquidation ou en difficulté

## Proof we can point to

- Migration de la comptabilité d'une fiduciaire genevoise de 25 personnes vers
  une solution infonuagique, sans interruption de service
- Automatisation du traitement des factures d'un bureau d'ingénieurs vaudois,
  douze heures par mois économisées

## How we open a conversation

Une observation concrète tirée du site de l'entreprise, une phrase sur ce que
nous avons fait pour une entreprise comparable, et une question ouverte. Jamais
de rendez-vous proposé d'emblée, jamais de tarif, jamais plus de cent
cinquante mots.
