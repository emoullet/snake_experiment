# Gestion de surcharge dans cartesian_manager

La session active la protection pour Explorer et Kinova avec `overload_protection:=true` au lancement de la stack. Les launches robot seuls restent désactivés par défaut. La calibration conserve son lancement habituel. `qontrol_controller` reste à `e365057`, sans ajout de protection dans son code.

## Déclenchement et arbitrage

Le manager écoute `/qontrol_explorer/effort_overload` (`std_msgs/msg/Bool`). Sur matériel réel, un Bool absent ou périmé après réception initiale produit un défaut verrouillé. Ce signal ne fournit ni force en newtons ni validation de son estimation : les diagnostics ne prétendent pas disposer de ces informations.

En simulation, la publication calculée par qontrol est remappée vers `/_session_control/calculated_effort_overload`, non écouté par le manager. Le matériel fictif ne reproduit pas les contacts. Le topic public sert aux injections ; une indication `true` expire après 0,5 s sans renouvellement, mais le verrou reste actif. Le manager attend le premier retour valide avant d'accepter les entrées, sans créer d'épisode de surcharge pendant le démarrage des contrôleurs.

Pendant le verrouillage, le manager rejette les commandes joystick/tablette/asservissement visuel, les modes et les nouvelles cibles. Il annule la cible articulaire active, vide les entrées mémorisées et publie exclusivement une vitesse de recul ou une vitesse nulle. Les transformations Baseline/Snake et le limiteur de téléopération ne sont pas appliqués au recul.

Le relais `gripper_gate_node` reste dans la stack. Il bloque les commandes utilisateur selon l'état du manager et envoie, si elle est récente, la dernière position mesurée de pince comme consigne de maintien. Une perte de télémétrie de protection ou de pince ferme également le relais.

## Stratégies et trajectoire

Le paramètre du manager `overload_retreat_strategy` est fixe au lancement. Il accepte :

- `closest_target_out` : les trois poses calibrées sont validées et transférées ensemble ; la cible la plus proche en distance euclidienne est choisie (première en cas d'égalité).
- `none` : verrouillage sans recul ni besoin de cibles calibrées.

La session utilise `closest_target_out`. Une interface de stratégie sépare la sélection de cible du déroulement commun. La calibration doit correspondre au robot, au repère de base et au repère effecteur sélectionnés. Sans calibration compatible, une surcharge verrouille sans recul. Les cibles ne peuvent pas changer pendant une récupération.

Pour `closest_target_out`, l'orientation retenue est le quaternion calibré ou celui-ci multiplié à droite par une rotation de π autour du Z local de l'outil, selon l'angle le plus court depuis la pose initiale. Le fichier de calibration reste inchangé.

1. Translation vers la cible en conservant l'orientation initiale.
2. Attente de 1 s de signal de surcharge valide et clair.
3. Rotation vers l'orientation adaptée, puis maintien verrouillé.

Limites : 0,02 m/s, 0,15 rad/s et 30 s au total ; tolérances 5 mm et 5°, maintien 0,5 s. Retour invalide/périmé, arrêt opérateur ou nouvelle surcharge pendant la rotation interrompent le recul. La cause initiale d'une erreur est conservée ; le retour des données valides ne relance pas le recul.

Le contrôleur historique multiplie ses entrées par ses gains de vitesse. Le manager compense ces gains et ses repères uniquement pour le recul. Les gains initiaux proviennent du même profil robot que qontrol ; les changements de gains et de sélection de repères sont suivis sur leurs topics existants. Un gain non positif/non fini ou un repère d'outil non pris en charge interdit le recul. Baseline/Snake conservent leurs commandes habituelles.

## Diagnostics, arrêt et réarmement

Le manager publie `/cartesian_manager/overload_status` (`DiagnosticArray`) à 20 Hz, avec instance, événement, cause, stratégie, état, validité du Bool et de la pose, cible, adaptation d'orientation, poses et historique des phases. Les watchdogs de réception utilisent une horloge monotone (délai 0,5 s), indépendamment de l’horloge Gazebo. Les états sont `normal`, `translating`, `waiting_clear`, `rotating`, `locked`, `error`.

Les services `Trigger` sont `/cartesian_manager/stop_recovery` et `/cartesian_manager/rearm_overload`. Les paramètres `overload_protection.reference_frame`, `ee_frame`, `out_positions` (9 nombres) et `out_orientations` (12 nombres, xyzw) sont adressés au manager par `set_parameters_atomically`.

Le réarmement exige un Bool valide et clair pendant 1 s (en simulation : aucune injection active), une pose valide et fraîche, un recul terminé ou arrêté, et un joystick neutre pendant 0,5 s ou silencieux environ 1 s. L'absence totale de joystick est acceptée après la temporisation neutre. Un message récent non neutre ou un bouton pressé bloque le réarmement.

Aucun mapper, Go-to ou enregistrement n'est restauré automatiquement. Après réarmement, activer explicitement le contrôle. Le journal `safety/overload_events.jsonl` et les interruptions d'essai conservent les mêmes emplacements ; les données d'essai ne deviennent pas valides grâce au recul. Les défauts de télémétrie sont distingués des surcharges.

## Compilation et relance

Arrêter l'ancienne interface et sa stack avec Ctrl+C, puis :

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
source /opt/ros/jazzy/setup.bash
source install/setup.bash
colcon build --base-paths /home/emoullet/dev/robot_experiments/snake_experiment/dependencies /home/emoullet/dev/robot_experiments/snake_experiment/src --packages-select cartesian_manager qontrol_controller snake_experiment_ui --symlink-install --parallel-workers 1 --allow-overriding cartesian_manager qontrol_controller
source install/setup.bash
ROS_DOMAIN_ID=91 ros2 launch snake_experiment_ui session_interface.launch.py stack_robot:=kinova stack_use_simulation:=true
```

Rafraîchir les pages avec Ctrl+F5. Pour le réel, utiliser `stack_use_simulation:=false` et `stack_robot_ip:=192.168.1.10` (adapter l'adresse).

Dans un second terminal sourcé sur le même workspace, uniquement pour la simulation :

```bash
ROS_DOMAIN_ID=91 ros2 topic pub --once /qontrol_explorer/effort_overload std_msgs/msg/Bool "{data: true}"
```

Vérifier coupure bras/pince, cible et orientation, recul, maintien verrouillé, disponibilité du réarmement avec joystick silencieux, puis absence de reprise automatique.

## Tests isolés et limites

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
ctest --test-dir build/cartesian_manager -R '^overload_protection$' --output-on-failure
```

Le script `scripts/check_overload_simulation.py` exige une stack Kinova protégée fictive. Il configure temporairement des cibles proches et injecte des commandes ; le lancer uniquement dans un domaine ROS libre, distinct de la session de l'utilisateur. Dans le terminal de stack sourcé :

```bash
ROS_DOMAIN_ID=173 ros2 launch snake_experiment_ui kinova.launch.py use_simulation:=true overload_protection:=true
```

Dans un autre terminal :

```bash
cd /home/emoullet/dev/robot_experiments/snake_experiment
source /opt/ros/jazzy/setup.bash
source /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641/install/setup.bash
ROS_DOMAIN_ID=173 python3 scripts/check_overload_simulation.py
```

Arrêter cette stack après le test pour effacer sa configuration temporaire. Les tests C++ couvrent aussi `none`, les limites, la conversion des vitesses et les défauts.

Le lot 2 examinera l'expiration des commandes dans qontrol si le manager tombe, le retour matériel et la validité de l'estimation d'effort. La version restaurée conserve potentiellement sa dernière consigne et publie une pose calculée depuis sa position de commande. Le Bool ne permet pas de distinguer une surcharge d'une estimation invalide. Cette migration ne constitue pas une protection indépendante du manager et ne planifie pas d'évitement d'obstacles. Les essais fictifs ne valident pas le comportement sur robot réel.

## Vérifications de cette migration

Exécutées : compilation des trois packages ; test C++ du manager ; scénarios ROS synthétiques pour les deux stratégies, la conversion des vitesses et les watchdogs de pose/Bool ; stack Kinova fictive avec arbitrage, pince, réarmement et arrêt opérateur ; recul Explorer sous Gazebo isolé ; interface web temporaire (alarme, commandes bloquées, réarmement sans joystick, aucun mapper restauré, aucune erreur JavaScript). La suite Python a 192 tests réussis et un échec préexistant sur la comparaison de la copie du launch Explorer avec son sous-module.

Les tests synthétiques sans robot se relancent dans un domaine libre :

```bash
cd /home/emoullet/dev/robot_experiments/snake_experiment
source /opt/ros/jazzy/setup.bash
source /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641/install/setup.bash
ROS_DOMAIN_ID=176 python3 scripts/check_manager_overload.py
```

À vérifier manuellement : reprise de votre session et ses transitions Baseline/Snake, essai interrompu pendant une acquisition, puis validation sur matériel réel dans un environnement adapté. La session utilisateur n'a pas été arrêtée ni modifiée pendant les essais isolés.
