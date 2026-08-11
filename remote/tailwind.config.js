/* Designtokens voor Iris Remote. Stond eerst inline in index.html naast een
 * cdn.tailwindcss.com-script dat de CSS in de browser compileerde; dat kost op
 * mobiel een flits ongestileerde pagina en zet een script van derden op de
 * pagina waar je publicaties goedkeurt. Nu bouwen we vooraf: `npm run build:css`.
 */
import forms from '@tailwindcss/forms';
import containerQueries from '@tailwindcss/container-queries';

export default {
  darkMode: 'class',
  content: ['./index.html', './app.js'],
  theme: {
    extend: {
      colors: {
        // Iris-violet — dezelfde identiteit als de aperture-mascotte in de
        // desktop-onboarding (frontend/js/tabs-onboarding.js:_irisMascot),
        // niet het generieke Google-blauw waar deze app mee begon. Surfaces
        // schuiven een fractie mee (violet-getint grijs i.p.v. blauwgrijs) op
        // exact dezelfde helderheidstrap, zodat bestaande contrastverhoudingen
        // niet omslaan.
        surface: '#121118', background: '#121118',
        'surface-variant': '#34313c', 'surface-container': '#1e1c26',
        'surface-container-low': '#1a1820', 'surface-container-lowest': '#0d0c12',
        'surface-container-high': '#28242f', 'surface-container-highest': '#34313c',
        primary: '#9c8fff', 'on-primary': '#15161c',
        'primary-container': '#7c6fe8', 'on-primary-container': '#ece9ff',
        secondary: '#c9c3e0', 'on-secondary': '#2a2640',
        'secondary-container': '#3e3a55', tertiary: '#d6c5e6',
        error: '#ffb4ab', 'error-container': '#93000a',
        // Nooit in de Tailwind-config gestaan — elke `text-warn` in de app
        // (agenda-drukte, achterstallige mail) compileerde stil naar niets en
        // erfde de gewone tekstkleur. Zelfde amber als --warn in style.css.
        // Semantische kleuren (ok/warn/err) blijven bewust los van de
        // merkkleur hierboven — een status mag nooit met de accentkleur
        // concurreren.
        warn: '#fbbf24',
        'on-surface': '#e3e0e6', 'on-surface-variant': '#c8c2d1',
        'on-background': '#e3e0e6', outline: '#928da0', 'outline-variant': '#453f4f',
      },
      borderRadius: { DEFAULT: '0.25rem', lg: '0.5rem', xl: '0.75rem', full: '9999px' },
      spacing: {
        unit: '4px', 'container-padding': '20px', gutter: '16px',
        'stack-sm': '8px', 'stack-md': '16px', 'stack-lg': '32px',
      },
      fontFamily: {
        'headline-md': ['Inter'], 'headline-sm': ['Inter'], 'body-md': ['Inter'],
        'body-lg': ['Inter'],
        // Display krijgt het instrument-readout-gevoel van de Control Room
        // i.p.v. dezelfde Inter-koppen als de rest van de tekst — hergebruikt
        // het al zelf-gehoste JetBrains Mono (label-caps), geen nieuw font.
        'display-lg': ['JetBrains Mono'], 'display-lg-mobile': ['JetBrains Mono'],
        'label-caps': ['JetBrains Mono'],
      },
      fontSize: {
        'headline-md': ['24px', { lineHeight: '32px', letterSpacing: '-0.01em', fontWeight: '600' }],
        'headline-sm': ['20px', { lineHeight: '28px', fontWeight: '600' }],
        'body-md': ['14px', { lineHeight: '20px', fontWeight: '400' }],
        'body-lg': ['16px', { lineHeight: '24px', fontWeight: '400' }],
        // JetBrains Mono is alleen in 400/500 gesubset (build-fonts.mjs) —
        // 700 zou hier synthetisch vetgedrukt worden door de browser. Mono
        // heeft ook geen negatieve tracking nodig (elk teken is al even
        // breed); -0.02em van de oude Inter-stijl liet cijfers/letters
        // botsen op 32px.
        'display-lg': ['32px', { lineHeight: '40px', letterSpacing: '0', fontWeight: '500' }],
        'display-lg-mobile': ['28px', { lineHeight: '36px', letterSpacing: '0', fontWeight: '500' }],
        'label-caps': ['12px', { lineHeight: '16px', letterSpacing: '0.05em', fontWeight: '500' }],
      },
    },
  },
  plugins: [forms, containerQueries],
};
