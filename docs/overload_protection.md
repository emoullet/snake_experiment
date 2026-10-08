# Protection de surcharge en session

La protection s’applique à Explorer et Kinova dans l’interface de session, y compris pendant le check-up. La calibration conserve son lancement actuel. Les stacks lancées par la session passent `overload_protection:=true` ; les launches robot seuls gardent ce paramètre à `false` par défaut.

## Déclenchement et recul

`/qontrol_explorer/effort_overload` est un `std_msgs/msg/Bool`. Le contrôleur détecte sa propre surcharge dans sa boucle de contrôle ; il accepte aussi une indication externe `true` sur ce topic. Une indication externe `false` ne masque jamais une surcharge mesurée.

Le premier déclenchement verrouille les commandes cartésiennes, articulaires, de repère et de vitesse. Le mapper appartenant à la session est arrêté, les Go-to ordinaires sont annulés et le relais de pince bloque les commandes utilisateur. La pince reçoit une consigne de maintien issue d’une position récemment mesurée. Une perte de son état ou de l’état de protection ferme également ce relais.

Le contrôleur choisit parmi `target_out_1`, `target_out_2`, `target_out_3` la position la plus proche en distance euclidienne. En cas d’égalité, il conserve le premier indice. La calibration doit appartenir au robot sélectionné, à son repère effecteur et à `base_link`. Les trois cibles sont transférées dans une seule transaction ROS. Sans trois cibles valides, la surcharge produit uniquement un maintien verrouillé.

L’orientation choisie est le quaternion calibré ou ce quaternion multiplié à droite par une rotation de π autour de **Z local de l’outil**, selon le plus petit angle à l’orientation initiale. Les quaternions `q` et `-q` sont équivalents. L’adaptation reste propre à cet épisode : le fichier de calibration n’est pas modifié.

Le recul comporte deux phases :

1. Translation vers la cible, en conservant l’orientation initiale.
2. Après une seconde de force valide et sans surcharge, rotation vers l’orientation choisie, en maintenant la position cible.

Les limites maximales sont 0,02 m/s et 0,15 rad/s. Les tolérances sont 5 mm et 5°, avec un maintien de 0,5 s. Le délai global est de 30 s. Un arrêt opérateur, une estimation invalide, des états articulaires périmés, une erreur du solveur ou une nouvelle surcharge pendant la rotation interrompent le recul et maintiennent le verrou. Une surcharge persistante pendant la translation ne déclenche pas plusieurs reculs.

Le contrôleur protège le bras indépendamment du navigateur et du nœud d’interface. Le relais de pince appartient à la stack, et non au serveur web. Il faut laisser cette stack et ses contrôleurs actifs pour effectuer le recul. Cette trajectoire cartésienne ne planifie pas d’évitement d’obstacles : la position « out » choisie doit être appropriée à l’environnement expérimental.

## État, arrêt et réarmement

`/qontrol_explorer/overload_status` publie un `diagnostic_msgs/msg/DiagnosticArray` horodaté. Il distingue la validité de l’estimation, la validité de la pose, le verrou, la phase, l’instance du contrôleur et le numéro d’épisode. Les états sont `normal`, `translating`, `waiting_clear`, `rotating`, `locked`, `error`.

Les services `std_srvs/srv/Trigger` sont :

- `/qontrol_explorer/stop_recovery` : arrêt du recul, maintien du verrou.
- `/qontrol_explorer/rearm_overload` : demande de réarmement explicitement vérifiée dans le contrôleur.

Le réarmement exige une force valide et claire pendant 1 s, les axes joystick 0 et 1 dans la zone neutre et tous les boutons relâchés pendant 0,5 s, des `/joint_states` complets et récents, et un recul terminé ou arrêté. Les messages de joystick et d’état doivent continuer à arriver. Aucun mapper, Go-to ou enregistrement précédent n’est restauré par le réarmement.

L’interface affiche l’alarme, la cible, l’adaptation éventuelle de l’orientation, l’arrêt et le réarmement. Les routes de commande refusent également les actions incompatibles. Après réarmement, réactiver explicitement le mode dans le check-up, ou **Activate control** dans Training/Recording. En Discovery, utiliser le bouton habituel d’activation du contrôle et de l’enregistrement.

L’essai en cours est marqué interrompu avant la finalisation du rosbag, avec un incident `effort_overload` à décrire. Sa réussite n’est pas créditée pendant le recul. Les segments Discovery interrompus sont incomplets et invalides. La revue d’incidents reste disponible lorsque les commandes sont verrouillées.

Le journal `safety/overload_events.jsonl` se trouve dans le dossier participant, ou dans `repository_root` pendant le check-up. L’historique des phases est conservé dans le contrôleur, y compris les transitions plus courtes que la période de publication ROS. Le journal enregistre les transitions, la pose initiale, les cibles originale et adaptée, le résultat et le réarmement, séparément des données des essais.

## Estimation de force et matériel réel

L’estimation utilise la Jacobienne linéaire et les couples articulaires compensés en gravité. La convention implémentée est `effort + gravité` pour Kinova et `effort - gravité` pour Explorer. La validité exige des valeurs finies et une Jacobienne de rang trois. Le seuil existant `force_threshold` reste à 10 N par défaut.

**Les signes, la compensation de gravité et le seuil doivent encore être validés sur les deux robots réels.** À chaque démarrage réel, `overload_protection.force_estimation_verified` est `false` : le contrôleur maintient le verrou et n’effectue pas de recul fondé sur une estimation non validée. L’état public expose la norme calculée, même si l’estimation n’a pas encore été confirmée.

Après avoir validé les couples, les forces et l’absence de faux déclenchements dans les poses utilisées, confirmer explicitement cette vérification :

```bash
source /opt/ros/jazzy/setup.bash
source /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641/install/setup.bash
ros2 param set /qontrol_explorer overload_protection.force_estimation_verified true
```

Puis remettre le joystick au neutre, attendre les délais de validité et utiliser **Rearm protection**. Cette confirmation est perdue lorsque le contrôleur est recréé.

En simulation, l’estimation de contact est neutralisée (`force_source=fake_hardware_no_contact`) ; les essais de surcharge se font par injection de `Bool`. Le Kinova simulé utilise du matériel fictif. Ces essais ne valident pas les efforts physiques ni la sécurité de la trajectoire réelle.

Les paramètres de recul peuvent être abaissés, mais pas dépasser les plafonds ci-dessus : `overload_protection.linear_speed`, `overload_protection.angular_speed`, `overload_protection.timeout_sec`. La configuration de cibles ne peut pas changer pendant un recul. L’identité du robot et l’activation de la protection restent fixes au lancement.

## Reconstruction et relance

Compiler **les deux packages**, car la coupure du bras est dans le sous-module `dependencies/qontrol_controller` :

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
source /opt/ros/jazzy/setup.bash
source install/setup.bash
colcon build \
  --base-paths /home/emoullet/dev/robot_experiments/snake_experiment/dependencies /home/emoullet/dev/robot_experiments/snake_experiment/src \
  --packages-select qontrol_controller snake_experiment_ui \
  --symlink-install --parallel-workers 1 --allow-overriding qontrol_controller
source install/setup.bash
```

Arrêter les anciennes interfaces/stacks, puis lancer la session avec une calibration du robot choisi :

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch snake_experiment_ui session_interface.launch.py \
  stack_robot:=kinova stack_use_simulation:=true \
  calibration_file:=/chemin/absolu/vers/latest_calib.json
```

Rafraîchir `http://127.0.0.1:8081` avec Ctrl+F5 et démarrer la stack depuis la page. Pour Explorer, remplacer `stack_robot:=kinova` par `stack_robot:=explorer`. Pour le Kinova réel, utiliser `stack_use_simulation:=false stack_robot_ip:=192.168.1.10`, avec l’IP effective du bras, puis suivre la validation d’estimation décrite ci-dessus.

## Tests reproductibles

Tests Python :

```bash
cd /home/emoullet/dev/robot_experiments/snake_experiment
source /opt/ros/jazzy/setup.bash
source /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641/install/setup.bash
ROS_LOG_DIR=/tmp/snake-overload-tests PYTHONPATH="$PWD/src/snake_experiment_ui:$PYTHONPATH" \
  python3 -m pytest src/snake_experiment_ui/test -q
```

Test C++ du recul :

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
ctest --test-dir build/qontrol_controller -R '^overload_protection$' --output-on-failure
```

Test ROS automatique, **uniquement avec un Kinova fictif**, sans autre stack dans le domaine 91. Il remplace les cibles de recul par des cibles de test proches et refuse de publier des commandes si l’état ne confirme pas le matériel fictif protégé.

Terminal 1 :

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ROS_DOMAIN_ID=91 ros2 launch snake_experiment_ui kinova.launch.py \
  use_simulation:=true overload_protection:=true
```

Terminal 2 :

```bash
cd /home/emoullet/dev/robot_experiments/snake_experiment
source /opt/ros/jazzy/setup.bash
source /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641/install/setup.bash
ROS_DOMAIN_ID=91 python3 scripts/check_overload_simulation.py
```

Ce test vérifie le recul, la stabilité de la pince malgré les commandes adverses, le verrouillage et l’absence de reprise des anciennes commandes après réarmement. Arrêter ensuite cette stack avec Ctrl+C ; la relancer recharge la calibration de session.

## Vérifications effectuées et restantes

Exécutées : compilation des deux packages, test C++, tests Python de protection et de sessions, scénario ROS Kinova fictif, suspension/reprise du broadcaster articulaire avec refus de réarmement sur données périmées, et vérification du navigateur (alarme, commandes désactivées, arrêt, réarmement, absence d’erreurs JavaScript). Un échec Python préexistant compare la copie d’un ancien launch Explorer au launch actuellement présent dans le sous-module `cartesian_manager`.

À vérifier sur le matériel réel : estimation et seuil de force pour chaque robot, maintien de la pince en mouvement, trajectoire de translation puis rotation dans l’environnement de l’expérience, limites articulaires et cible irréalisable, comportement sous charge et arrêt opérateur. Le lancement et le recul Explorer en simulation complète restent également à tester.
