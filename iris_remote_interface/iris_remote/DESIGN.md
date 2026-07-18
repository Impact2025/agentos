---
name: Iris Remote
colors:
  surface: '#101415'
  surface-dim: '#101415'
  surface-bright: '#363a3b'
  surface-container-lowest: '#0b0f10'
  surface-container-low: '#191c1e'
  surface-container: '#1d2022'
  surface-container-high: '#272a2c'
  surface-container-highest: '#323537'
  on-surface: '#e0e3e5'
  on-surface-variant: '#bdc8d1'
  inverse-surface: '#e0e3e5'
  inverse-on-surface: '#2d3133'
  outline: '#87929a'
  outline-variant: '#3e484f'
  surface-tint: '#7bd0ff'
  primary: '#8ed5ff'
  on-primary: '#00354a'
  primary-container: '#38bdf8'
  on-primary-container: '#004965'
  inverse-primary: '#00668a'
  secondary: '#b9c8de'
  on-secondary: '#233143'
  secondary-container: '#39485a'
  on-secondary-container: '#a7b6cc'
  tertiary: '#c5cce6'
  on-tertiary: '#283044'
  tertiary-container: '#a9b1ca'
  on-tertiary-container: '#3c4459'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#c4e7ff'
  primary-fixed-dim: '#7bd0ff'
  on-primary-fixed: '#001e2c'
  on-primary-fixed-variant: '#004c69'
  secondary-fixed: '#d4e4fa'
  secondary-fixed-dim: '#b9c8de'
  on-secondary-fixed: '#0d1c2d'
  on-secondary-fixed-variant: '#39485a'
  tertiary-fixed: '#dae2fd'
  tertiary-fixed-dim: '#bec6e0'
  on-tertiary-fixed: '#131b2e'
  on-tertiary-fixed-variant: '#3f465c'
  background: '#101415'
  on-background: '#e0e3e5'
  surface-variant: '#323537'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
  display-lg-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 4px
  container-padding: 20px
  gutter: 16px
  stack-sm: 8px
  stack-md: 16px
  stack-lg: 32px
---

## Brand & Style

The design system is engineered for executive-level decision-making, where clarity and speed are paramount. It adopts a **Sovereign Minimalism** style—a fusion of deep-space backgrounds and precision-engineered functional elements. The brand personality is sophisticated and unflappable, designed to feel like a high-end digital concierge that stays out of the way until needed.

The visual narrative relies on high-contrast focus areas and an asynchronous-first communication flow. By combining a "dark mode by default" ethos with surgical applications of neon light, the interface reduces cognitive load while maintaining an elite, premium feel. 

**Design Principles:**
- **Zero Friction:** Every tap is intentional; every transition is purposeful.
- **Authority through Contrast:** Use deep blacks to recede and bright iridescence to command attention.
- **Glass Precision:** Use translucent layers to provide spatial context without cluttering the view.

## Colors

This design system utilizes a high-contrast dark palette to maximize legibility and minimize eye strain during extended executive use.

- **Primary (Iris Blue):** Reserved exclusively for active states, primary actions, and critical status updates. It should be used sparingly to maintain its "glow" effect against the dark backdrop.
- **Surface (Deep Slate):** The foundational #0F172A background provides a sophisticated, non-black canvas that allows for subtle shadow depth.
- **Secondary (Muted Slate):** Used for supporting text and non-interactive icons to ensure visual hierarchy.
- **Neutral (Cloud White):** Used for high-priority text content and titles to provide maximum contrast.
- **Accents:** Use a 10% opacity version of Iris Blue for background washes on active cards or glassmorphic overlays.

## Typography

The typography system prioritizes "Information Density Control." We use **Inter** for its neutral, highly legible character in UI applications. To add a touch of technical sophistication and "executive assistant" precision, **JetBrains Mono** is introduced for labels, timestamps, and metadata.

- **Headlines:** Use tight letter spacing (-0.02em) for large displays to create a more compact, authoritative look.
- **Labels:** Always use JetBrains Mono for system-generated data, such as "AGENT STATUS: ONLINE" or timestamps, to distinguish human content from system logic.
- **Hierarchy:** Maintain a clear distinction between white primary text and slate-400 secondary text to guide the executive's eye to the most important data first.

## Layout & Spacing

The layout follows a **Fixed-Fluid Hybrid** model. For mobile, we utilize a 4-column grid with 20px side margins to ensure tap targets are never too close to the screen edge. On larger displays, content is centered within a 768px maximum-width container to maintain focus.

- **Vertical Rhythm:** Use a strict 4px baseline grid. All heights and vertical margins must be multiples of 4.
- **Asynchronous Stacks:** Components are arranged in vertical stacks. Use `stack-lg` to separate distinct "task groups" and `stack-sm` for elements within a task (e.g., a header and its description).
- **Safe Areas:** Ensure all primary CTA buttons are docked within the bottom safe area but visually floating via glassmorphism.

## Elevation & Depth

Elevation in this design system is expressed through transparency and "light-bleed" rather than traditional heavy shadows.

- **The Base:** The #0F172A background is the furthest layer back.
- **Glass Layers:** Cards and modals use a backdrop filter (`blur: 12px`) with a semi-transparent slate fill (80% opacity). This creates a sense of "physicality" where the background colors subtly bleed through.
- **Crisp Borders:** Instead of shadows, use a 1px solid border at 10% white opacity for containers. For active elements, use a 1px border of Iris Blue.
- **Subtle Glow:** Only the "Active" status and primary buttons receive a soft, colored outer glow (`box-shadow: 0 0 15px rgba(56, 189, 248, 0.3)`) to simulate a high-tech interface.

## Shapes

The shape language is "Calculated Softness." Elements are rounded enough to feel modern and accessible, but not so circular as to appear "playful" or consumer-grade.

- **Primary Containers:** 0.5rem (8px) corner radius. This applies to cards and input fields.
- **Interactive Elements:** Buttons follow the `rounded-lg` (1rem) rule to provide a distinct tactile shape compared to static containers.
- **Indicator Shapes:** Status dots and small avatars use 100% (pill/circle) rounding to denote "living" or "active" entities.

## Components

### Buttons
- **Primary:** Solid Iris Blue fill with Cloud White text. High-contrast.
- **Secondary:** Transparent background with a 1px white-alpha border.
- **Ghost:** No border or fill, Iris Blue text, used for less frequent actions like "Cancel."

### Cards
- Always glassmorphic. Background: `rgba(15, 23, 42, 0.8)`. 
- Border: `1px solid rgba(255, 255, 255, 0.1)`.
- Use for grouping related assistant tasks or data points.

### Inputs
- Background is slightly darker than the base surface (#020617).
- Border turns Iris Blue on focus with a subtle inner glow.
- Labels are always JetBrains Mono, All-Caps, 12px.

### Status Chips
- Small, pill-shaped elements. 
- Use a "Dot + Text" pattern. The dot should pulse slightly if the Agent is currently processing information.

### Lists
- Separated by subtle 1px lines (`rgba(255, 255, 255, 0.05)`).
- Chevron indicators are used only for navigational items, never for static data.