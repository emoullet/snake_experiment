(() => {
  const fr = {
    'Experiment': 'Expérience', 'Experiment · Participant': 'Expérience · Participant',
    'Connecting': 'Connexion', 'Connected': 'Connecté', 'Reconnecting': 'Reconnexion',
    'Participant screen': 'Écran participant', 'Waiting for the experimenter': 'En attente de l’expérimentateur',
    'Instructions will appear here when the experimenter is ready.': 'Les instructions apparaîtront ici lorsque l’expérimentateur sera prêt.',
    'Mode explanation': 'Explication du mode', 'Experiment introduction': 'Présentation de l’expérience',
    'Experiment explanation': 'Explication de l’expérience', 'Mode explanation video': 'Vidéo d’explication du mode',
    'Experiment presentation video': 'Vidéo de présentation de l’expérience',
    'The configured mode video could not be prepared for playback. Please notify the experimenter.': 'La vidéo du mode ne peut pas être lue. Veuillez prévenir l’expérimentateur.',
    'The configured video could not be prepared for playback. Please notify the experimenter.': 'La vidéo ne peut pas être lue. Veuillez prévenir l’expérimentateur.',
    'Automatic playback was blocked. Press Play to start the video with sound.': 'La lecture automatique a été bloquée. Appuyez sur Lecture pour démarrer la vidéo avec le son.',
    'Mode explanation video unavailable': 'Vidéo d’explication du mode indisponible',
    'The experimenter may continue for development only.': 'L’expérimentateur peut continuer uniquement pour le développement.',
    'Presentation video is not available yet. The experimenter may continue for development only.': 'La vidéo de présentation n’est pas encore disponible. L’expérimentateur peut continuer uniquement pour le développement.',
    'Current task': 'Tâche actuelle', 'Training': 'Entraînement', 'Waiting': 'En attente',
    'Waiting for next trial': 'En attente du prochain essai', 'Preparing trial': 'Préparation de l’essai',
    'Time remaining': 'Temps restant', 'Time limit reached. Please wait for the experimenter.': 'Temps écoulé. Veuillez attendre l’expérimentateur.',
    'Linear distance': 'Distance linéaire', 'Angular distance': 'Distance angulaire',
    'Webcam live feed': 'Image de la webcam', 'Camera preview is off.': 'La caméra est désactivée.',
    'Waiting for camera access…': 'En attente de l’accès à la caméra…', 'Camera preview is unavailable.': 'La caméra est indisponible.',
    'Camera preview is unavailable. You may continue without it.': 'La caméra est indisponible. Vous pouvez continuer sans elle.',
    'Retry camera': 'Réessayer la caméra', 'Enable success sound': 'Activer le son de réussite', 'Play warning sound': 'Jouer le son d’alerte',
    'Current control mode': 'Mode de commande actuel', 'Unknown': 'Inconnu', 'Joystick mapping': 'Correspondance du joystick',
    'Show text': 'Afficher le texte', 'Show demo': 'Afficher la démonstration',
    'Current joystick mapping': 'Correspondance actuelle du joystick', 'Current joystick mapping explanation': 'Explication de la correspondance actuelle du joystick',
    'Waiting for the current control mode.': 'En attente du mode de commande actuel.', 'Left/right input': 'Commande gauche/droite',
    'Forward/backward input': 'Commande avant/arrière', 'Joystick mapping · example inputs': 'Correspondance du joystick · exemples de commandes',
    'Robot preview viewpoint': 'Point de vue du robot', 'Waiting for the current mapper state.': 'En attente de l’état des commandes.',
    'Horizontal input': 'Commande horizontale', 'Vertical input': 'Commande verticale',
    'Animated robot arm for horizontal input': 'Bras robot animé pour la commande horizontale', 'Animated robot arm for vertical input': 'Bras robot animé pour la commande verticale',
    'Illustrative horizontal joystick input': 'Illustration de la commande horizontale du joystick', 'Illustrative vertical joystick input': 'Illustration de la commande verticale du joystick',
    'Recording': 'Enregistrement', 'Target reached': 'Cible atteinte',
    'Force overload detected. Commands paused. Automatic retreat in progress.': 'Surcharge de force détectée. Commandes suspendues. Recul automatique en cours.',
    'Force overload detected. Commands paused. Automatic retreat will begin shortly.': 'Surcharge de force détectée. Commandes suspendues. Le recul automatique va commencer.',
    'Commands paused. Retreat paused while contact force clears.': 'Commandes suspendues. Recul en pause en attendant la fin de la force de contact.',
    'Retreat complete. Commands remain paused. Please wait for the experimenter.': 'Recul terminé. Les commandes restent suspendues. Veuillez attendre l’expérimentateur.',
    'Force overload detected. Commands paused. Please wait for the experimenter.': 'Surcharge de force détectée. Commandes suspendues. Veuillez attendre l’expérimentateur.',
    'Commands paused. Please wait for the experimenter.': 'Commandes suspendues. Veuillez attendre l’expérimentateur.',
    'Demo pose': 'Pose de démonstration', 'Live pose': 'Pose en direct',
    'Demo pose · joint states are unavailable or stale.': 'Pose de démonstration · états articulaires indisponibles ou périmés.',
    'Preparing mapping animation.': 'Préparation de l’animation des commandes.',
    'Mapping configuration unavailable · animations cannot start.': 'Configuration des commandes indisponible · les animations ne peuvent pas démarrer.',
    'Coupling unknown': 'Couplage inconnu', 'Coupling active': 'Couplage actif', 'Coupling inactive': 'Couplage inactif',
    'No feasible movement from this pose; the view remains neutral.': 'Aucun mouvement possible depuis cette pose ; la vue reste neutre.',
  };
  let language = 'english';
  let chosen = null;
  try { const value = localStorage.getItem('participant_language'); if (['english', 'french'].includes(value)) chosen = value; } catch {}
  const reverse = Object.fromEntries(Object.entries(fr).map(([en, french]) => [french, en]));
  function translate(value) {
    if (typeof value !== 'string') return value;
    if (language !== 'french') return value;
    if (fr[value]) return fr[value];
    let match;
    if ((match = value.match(/^View (\d+)$/))) return `Vue ${match[1]}`;
    if ((match = value.match(/^Go to target (.+)$/))) return `Aller à la cible ${match[1]}`;
    if ((match = value.match(/^Controller ([AB]) mode explanation$/))) return `Explication du mode de commande ${match[1]}`;
    if ((match = value.match(/^Controller ([AB]) (training|recording)$/))) return `Commande ${match[1]} · ${match[2] === 'training' ? 'entraînement' : 'enregistrement'}`;
    if ((match = value.match(/^Illustrative (.+) mapping; mapper sub-mode unavailable\.$/))) return `Correspondance ${match[1]} illustrative ; sous-mode des commandes indisponible.`;
    if ((match = value.match(/^Movement reduced to (\d+)% due to reachability or joint limits\.$/))) return `Mouvement réduit à ${match[1]} % en raison des limites de portée ou articulaires.`;
    if ((match = value.match(/^Mapping animation is unavailable: (.*)$/))) return `Animation des commandes indisponible : ${match[1]}`;
    if ((match = value.match(/^Axis (\d+) · (.+) → neutral → (.+) → neutral$/))) {
      const directions = { Right: 'droite', Left: 'gauche', Up: 'haut', Down: 'bas' };
      return `Axe ${match[1]} · ${directions[match[2]] || match[2]} → neutre → ${directions[match[3]] || match[3]} → neutre`;
    }
    if ((match = value.match(/^(.+) animated robot, axis (\d+)$/))) return `Robot ${match[1]} animé, axe ${match[2]}`;
    if ((match = value.match(/^Axis (\d+)$/))) return `Axe ${match[1]}`;
    return value;
  }
  const staticNodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) { const node = walker.currentNode; if (node.nodeValue.trim()) staticNodes.push([node, node.nodeValue]); }
  const staticAttrs = [...document.querySelectorAll('[aria-label]')].map(el => [el, el.getAttribute('aria-label')]);
  const select = document.getElementById('participant-language');
  function apply() {
    document.documentElement.lang = language === 'french' ? 'fr' : 'en';
    document.title = translate('Experiment · Participant');
    select.value = language;
    for (const [node, initial] of staticNodes) {
      // Dynamic nodes are refreshed by their renderers after this pass.
      const current = node.nodeValue.trim();
      const original = reverse[current] || initial.trim();
      node.nodeValue = initial.replace(initial.trim(), translate(original));
    }
    for (const [el, original] of staticAttrs) el.setAttribute('aria-label', translate(original));
  }
  function choose(value, persist = false) {
    if (!['english', 'french'].includes(value)) value = 'english';
    language = value;
    if (persist) { chosen = value; try { localStorage.setItem('participant_language', value); } catch {} }
    apply();
    window.dispatchEvent(new CustomEvent('participant:language', { detail: language }));
  }
  select.addEventListener('change', () => choose(select.value, true));
  window.participantI18n = { t: translate, get language() { return language; }, initialize: value => choose(chosen || value || 'english') };
  choose(chosen || 'english');
})();
