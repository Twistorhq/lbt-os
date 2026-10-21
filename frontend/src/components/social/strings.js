/**
 * Twistor Social string catalog (TW-652).
 *
 * Every member-facing string ships from this catalog in English and
 * Spanish at parity (TW-646 Section 3). No component hardcodes copy.
 * The two catalogs carry identical key sets; the parity test in
 * __tests__/strings.test.js fails the build if they drift.
 *
 * The consent strings below are a faithful plain-language rendering of
 * the elements Angela's TW-654 package requires on these screens
 * (Section 1.4, item 1): what is captured, the exclusive purposes, the
 * retention and destruction schedule as amended by D2, the no-sale
 * covenant, withdrawal, and band-only age handling. Final consent text
 * is subject to the outside counsel review authorized under D1, and
 * the Spanish rendering is subject to the human translation review
 * TW-646 Section 3 requires; both flags are filed on TW-652.
 *
 * Prepared by Twistor Holdings LLC
 */

// The versions recorded with every grant in vfy_consents
// (policy_version, retention_schedule_version). Bump only when Angela
// publishes a new version of the text or the schedule; the version a
// member saw is a legal record, never an implementation detail.
export const BIOMETRIC_POLICY_VERSION = '1.0'
export const RETENTION_SCHEDULE_VERSION = '1.0'

export const SUPPORTED_LOCALES = ['en', 'es']

export function getStrings(locale) {
  return STRINGS[locale] || STRINGS.en
}

/** Fill {name} placeholders in a catalog string. */
export function fill(template, vars) {
  return template.replace(/\{(\w+)\}/g, (match, key) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : match,
  )
}

export const STRINGS = {
  en: {
    common: {
      notNow: 'Not now',
      continue: 'Continue',
      stepConsent: 'Step 1 of 4: Consent',
      doorBadge: 'Checked at the door',
    },
    chip: {
      aria21: 'Age band: 21 plus',
      aria18: 'Age band: 18 to 20',
      ariaTeen: 'Teen account',
    },
    explain: {
      title: 'The door check, before your first night out',
      lede: 'Everyone on Twistor Social passes the same ID check, the way a good door checks everyone in line. It happens once, here, before the night starts.',
      whatTitle: 'What we capture',
      documentLabel: 'Your ID',
      document:
        'A photo of your government ID, front and back, or the photo page of your passport.',
      selfieLabel: 'A live selfie',
      selfie:
        'A short live camera check, so we can match your face to the ID.',
      templateLabel: 'A face template',
      template:
        'From the two, we build a face template: a mathematical map of your face. The template is the thing we keep.',
      whyTitle: 'Why we capture it',
      purposeVerify:
        'To confirm you are who you say you are, and which age band is yours. We record the band, never your birthdate.',
      purposeDuplicates:
        'To keep one person to one verified account, so nobody can return under a fresh signup after a ban.',
      purposeIntegrity:
        'To keep the room honest after the check, so the person who shows up is the person who verified.',
    },
    schedule: {
      title: 'What we keep, and when it is destroyed',
      photosLabel: 'Your ID and selfie photos',
      photos:
        'Destroyed when your check is decided, and never kept longer than 72 hours.',
      templateLabel: 'Your face template',
      template:
        'Kept only while your account stays verified. It stays inside the verification service, encrypted. Nothing outside that service ever receives it: the rest of Twistor Social only ever learns yes or no.',
      closureLabel: 'If you close your account',
      closure: 'Your template is destroyed with the account.',
      dormancyLabel: 'If you stop using Twistor Social',
      dormancy:
        'Your template is destroyed 24 months after your last activity. We notify you before it happens, your account returns to unverified, and you can verify again whenever you like.',
      withdrawLabel: 'If you withdraw consent',
      withdraw:
        'You can withdraw in Settings at any time. Your template is destroyed and your account returns to browsing only, the same as before you verified.',
      noSaleLabel: 'Never sold',
      noSale:
        'We never sell, lease, or trade your template or your photos. That promise stands in our published biometric policy, not just on this screen.',
      policyLink: 'Read the full biometric policy',
    },
    grant: {
      title: 'This consent stands on its own',
      body: 'It is separate from the Terms of Service. Agreeing to the terms does not give this consent, and this consent does not accept the terms.',
      summary:
        'You consent to the capture on the two screens before this one: an ID photo, a live selfie, and the face template built from them, kept and destroyed on the schedule you just read.',
      attest:
        'I understand what will be captured, why, how long it is kept, and when it is destroyed. I agree to the creation and keeping of my face template on that schedule.',
      agree: 'I agree. Start the check',
      versionLine:
        'Biometric consent, policy version {policyVersion}. Retention schedule version {retentionVersion}. The version you saw is recorded with your consent.',
    },
    declined: {
      title: 'No problem at all',
      body: 'Nothing was captured. No photo, no template, nothing to delete.',
      body2:
        'You can keep browsing public events and groups. The check stays here if you change your mind, and we will not keep asking.',
      browse: 'Back to browsing',
    },
    capture: {
      title: 'Now the check itself',
      body: 'Your camera opens next: first your ID, then the live selfie. Retakes are free and unpenalized, as many as you need.',
      guidance:
        'Good light helps the most. If the camera check cannot read you, that is a limit of the camera, never a verdict on you.',
      start: 'Open the camera check',
      assistedNote:
        'If the live challenge is not possible for you, a trained reviewer can run the check with assisted capture instead.',
      assisted: 'Use human review instead',
    },
    pending: {
      title: 'A person is taking a look',
      body: 'Your check was routed to a trained reviewer. That is a normal part of this process, and it happens for every kind of reason, most of them about lighting and camera quality.',
      body2:
        'We notify you the moment there is a decision. Until then, you can keep browsing public events and groups.',
      browse: 'Keep browsing',
    },
    approved: {
      title21: 'You are verified 21+',
      title18: 'You are verified 18+',
      titleTeen: 'You are verified',
      body: 'The room sees your band, never your birthdate. Your ID and selfie photos are destroyed on the schedule you agreed to; your template stays in the verification service, on that same schedule.',
      start: 'Start exploring',
    },
    rejected: {
      title: 'We could not verify you this time',
      body: 'That is a limit of the check, not a verdict on you, and it is never the last word.',
      retakeLabel: 'Try again with guidance',
      retake:
        'Retake the camera check. The guidance walks you through light, distance, and glare, and retakes are always free.',
      retakeAction: 'Retake the check',
      reviewLabel: 'Ask for a human review',
      review: 'A trained reviewer takes a fresh look at your check.',
      reviewAction: 'Request human review',
    },
    dormant: {
      title: 'Your verification has lapsed',
      dateLine: 'Your face template was destroyed on {date}, on the published schedule.',
      body: 'It had been 24 months since your last activity here. Your account is unverified again, which means browsing works and posting waits, the same as before you first verified.',
      body2:
        'Nothing else changed. Your profile, your groups, and your history are still yours.',
      reverify: 'Verify again',
    },
  },

  es: {
    common: {
      notNow: 'Ahora no',
      continue: 'Continuar',
      stepConsent: 'Paso 1 de 4: Consentimiento',
      doorBadge: 'Verificado en la puerta',
    },
    chip: {
      aria21: 'Banda de edad: 21 o más',
      aria18: 'Banda de edad: de 18 a 20',
      ariaTeen: 'Cuenta de adolescente',
    },
    explain: {
      title: 'El control de la puerta, antes de tu primera noche',
      lede: 'Toda persona en Twistor Social pasa el mismo control de identificación, como una buena puerta que revisa a toda la fila. Se hace una sola vez, aquí, antes de que empiece la noche.',
      whatTitle: 'Qué capturamos',
      documentLabel: 'Tu identificación',
      document:
        'Una foto de tu identificación oficial, frente y reverso, o la página con foto de tu pasaporte.',
      selfieLabel: 'Una selfie en vivo',
      selfie:
        'Una breve revisión con la cámara en vivo, para comparar tu rostro con la identificación.',
      templateLabel: 'Una plantilla facial',
      template:
        'Con ambas creamos una plantilla facial: un mapa matemático de tu rostro. La plantilla es lo que conservamos.',
      whyTitle: 'Para qué lo capturamos',
      purposeVerify:
        'Para confirmar que eres quien dices ser y cuál es tu banda de edad. Registramos la banda, nunca tu fecha de nacimiento.',
      purposeDuplicates:
        'Para mantener una sola cuenta verificada por persona, de modo que nadie pueda volver con un registro nuevo después de una expulsión.',
      purposeIntegrity:
        'Para mantener la honestidad del lugar después del control, de modo que quien se presenta sea la persona que se verificó.',
    },
    schedule: {
      title: 'Qué conservamos y cuándo se destruye',
      photosLabel: 'Las fotos de tu identificación y tu selfie',
      photos:
        'Se destruyen cuando se decide tu verificación y nunca se conservan más de 72 horas.',
      templateLabel: 'Tu plantilla facial',
      template:
        'Se conserva solo mientras tu cuenta siga verificada. Permanece dentro del servicio de verificación, cifrada. Nada fuera de ese servicio la recibe jamás: el resto de Twistor Social solo conoce un sí o un no.',
      closureLabel: 'Si cierras tu cuenta',
      closure: 'Tu plantilla se destruye con la cuenta.',
      dormancyLabel: 'Si dejas de usar Twistor Social',
      dormancy:
        'Tu plantilla se destruye 24 meses después de tu última actividad. Te avisamos antes de que ocurra, tu cuenta vuelve a estar sin verificar y puedes verificarte de nuevo cuando quieras.',
      withdrawLabel: 'Si retiras tu consentimiento',
      withdraw:
        'Puedes retirarlo en Ajustes en cualquier momento. Tu plantilla se destruye y tu cuenta vuelve a ser solo de lectura, igual que antes de verificarte.',
      noSaleLabel: 'Nunca se vende',
      noSale:
        'Nunca vendemos, arrendamos ni intercambiamos tu plantilla ni tus fotos. Esa promesa consta en nuestra política biométrica publicada, no solo en esta pantalla.',
      policyLink: 'Leer la política biométrica completa',
    },
    grant: {
      title: 'Este consentimiento vale por sí solo',
      body: 'Es independiente de los Términos de Servicio. Aceptar los términos no otorga este consentimiento, y este consentimiento no acepta los términos.',
      summary:
        'Aceptas la captura descrita en las dos pantallas anteriores: una foto de tu identificación, una selfie en vivo y la plantilla facial creada a partir de ellas, conservada y destruida según el calendario que acabas de leer.',
      attest:
        'Entiendo qué se capturará, para qué, cuánto tiempo se conservará y cuándo se destruirá. Acepto la creación y conservación de mi plantilla facial según ese calendario.',
      agree: 'Acepto. Empezar la verificación',
      versionLine:
        'Consentimiento biométrico, versión de política {policyVersion}. Versión del calendario de retención {retentionVersion}. La versión que viste queda registrada con tu consentimiento.',
    },
    declined: {
      title: 'Sin ningún problema',
      body: 'No se capturó nada. Ni foto, ni plantilla, nada que borrar.',
      body2:
        'Puedes seguir explorando los eventos y grupos públicos. La verificación seguirá aquí si cambias de opinión, y no te lo estaremos preguntando.',
      browse: 'Volver a explorar',
    },
    capture: {
      title: 'Ahora sí, la verificación',
      body: 'A continuación se abre tu cámara: primero tu identificación, después la selfie en vivo. Puedes repetir las tomas gratis y sin penalización, las veces que necesites.',
      guidance:
        'Buena luz es lo que más ayuda. Si la cámara no logra leerte, eso es un límite de la cámara, nunca un juicio sobre ti.',
      start: 'Abrir la verificación con cámara',
      assistedNote:
        'Si el desafío en vivo no es posible para ti, una persona capacitada puede hacer la verificación con captura asistida.',
      assisted: 'Usar revisión humana',
    },
    pending: {
      title: 'Una persona está revisando tu caso',
      body: 'Tu verificación pasó a una persona capacitada. Es una parte normal de este proceso y ocurre por todo tipo de razones, la mayoría relacionadas con la luz y la calidad de la cámara.',
      body2:
        'Te avisaremos en cuanto haya una decisión. Mientras tanto, puedes seguir explorando los eventos y grupos públicos.',
      browse: 'Seguir explorando',
    },
    approved: {
      title21: 'Verificación completa: 21+',
      title18: 'Verificación completa: 18+',
      titleTeen: 'Verificación completa',
      body: 'El lugar ve tu banda, nunca tu fecha de nacimiento. Las fotos de tu identificación y tu selfie se destruyen según el calendario que aceptaste; tu plantilla permanece en el servicio de verificación, con ese mismo calendario.',
      start: 'Empezar a explorar',
    },
    rejected: {
      title: 'Esta vez no pudimos verificarte',
      body: 'Eso es un límite de la verificación, no un juicio sobre ti, y nunca es la última palabra.',
      retakeLabel: 'Intentarlo de nuevo con guía',
      retake:
        'Repite la verificación con cámara. La guía te acompaña con la luz, la distancia y los reflejos, y repetir es siempre gratis.',
      retakeAction: 'Repetir la verificación',
      reviewLabel: 'Pedir una revisión humana',
      review: 'Una persona capacitada revisa tu verificación con ojos nuevos.',
      reviewAction: 'Solicitar revisión humana',
    },
    dormant: {
      title: 'Tu verificación venció',
      dateLine: 'Tu plantilla facial se destruyó el {date}, según el calendario publicado.',
      body: 'Habían pasado 24 meses desde tu última actividad aquí. Tu cuenta volvió a estar sin verificar: explorar funciona y publicar espera, igual que antes de tu primera verificación.',
      body2:
        'Nada más cambió. Tu perfil, tus grupos y tu historial siguen siendo tuyos.',
      reverify: 'Verificarme de nuevo',
    },
  },
}
