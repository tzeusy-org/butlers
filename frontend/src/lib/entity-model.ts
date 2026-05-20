// ---------------------------------------------------------------------------
// entity-model.ts -- canonical entity color hex map
//
// Spec discipline (dashboard-relationship/spec.md §"Dispatch design language
// token discipline"): hex literals are ONLY permitted here in the entity
// component tree. All other files in frontend/src/components/relationship/*,
// frontend/src/pages/entities/*, and frontend/src/pages/butlers/relationship/*
// MUST reference the constants below instead of writing inline hex.
//
// When adding a new entity color constant, add it here first, then reference
// it from components. Never introduce a new hex literal directly in a
// component file.
// ---------------------------------------------------------------------------

/**
 * White text color used on filled entity badge backgrounds.
 *
 * Used wherever a role badge (role-owner, role-admin, role-default),
 * state badge (state-unidentified), or label badge renders on a saturated
 * background color and needs legible white text. This is the canonical
 * single source of that literal so component files stay hex-free.
 */
export const ENTITY_BADGE_TEXT = "#fff"
