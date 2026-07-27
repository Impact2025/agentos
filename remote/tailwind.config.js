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
        surface: '#101415', background: '#101415',
        'surface-variant': '#323537', 'surface-container': '#1d2022',
        'surface-container-low': '#191c1e', 'surface-container-lowest': '#0b0f10',
        'surface-container-high': '#272a2c', 'surface-container-highest': '#323537',
        primary: '#8ed5ff', 'on-primary': '#00354a',
        'primary-container': '#38bdf8', 'on-primary-container': '#004965',
        secondary: '#b9c8de', 'on-secondary': '#233143',
        'secondary-container': '#39485a', tertiary: '#c5cce6',
        error: '#ffb4ab', 'error-container': '#93000a',
        'on-surface': '#e0e3e5', 'on-surface-variant': '#bdc8d1',
        'on-background': '#e0e3e5', outline: '#87929a', 'outline-variant': '#3e484f',
      },
      borderRadius: { DEFAULT: '0.25rem', lg: '0.5rem', xl: '0.75rem', full: '9999px' },
      spacing: {
        unit: '4px', 'container-padding': '20px', gutter: '16px',
        'stack-sm': '8px', 'stack-md': '16px', 'stack-lg': '32px',
      },
      fontFamily: {
        'headline-md': ['Inter'], 'headline-sm': ['Inter'], 'body-md': ['Inter'],
        'body-lg': ['Inter'], 'display-lg': ['Inter'], 'display-lg-mobile': ['Inter'],
        'label-caps': ['JetBrains Mono'],
      },
      fontSize: {
        'headline-md': ['24px', { lineHeight: '32px', letterSpacing: '-0.01em', fontWeight: '600' }],
        'headline-sm': ['20px', { lineHeight: '28px', fontWeight: '600' }],
        'body-md': ['14px', { lineHeight: '20px', fontWeight: '400' }],
        'body-lg': ['16px', { lineHeight: '24px', fontWeight: '400' }],
        'display-lg': ['32px', { lineHeight: '40px', letterSpacing: '-0.02em', fontWeight: '700' }],
        'display-lg-mobile': ['28px', { lineHeight: '36px', fontWeight: '700' }],
        'label-caps': ['12px', { lineHeight: '16px', letterSpacing: '0.05em', fontWeight: '500' }],
      },
    },
  },
  plugins: [forms, containerQueries],
};
