/**
 * Client ID Management
 * 
 * Each browser tab gets a unique client ID stored in sessionStorage.
 * This ensures complete isolation between different tabs/users.
 * 
 * - sessionStorage is tab-specific (not shared between tabs)
 * - ID persists for the lifetime of the tab
 * - New tab = new client ID = fresh isolated environment
 * 
 *  SINGLETON PATTERN: Ensures only one client ID per tab
 */

const CLIENT_ID_KEY = 'treadstone_client_id';

//  Module-level singleton to prevent race conditions
let cachedClientId = null;

/**
 * Generate a unique client ID
 * Format: client_{timestamp}_{random_string}
 */
function generateClientId() {
    const timestamp = Date.now();
    const randomPart = Math.random().toString(36).substring(2, 11);
    return `client_${timestamp}_${randomPart}`;
}

/**
 * Get or create client ID for this tab
 *  Uses module-level cache to prevent race conditions
 * @returns {string} Unique client ID for this browser tab
 */
export function getClientId() {
    // Return cached value if available (prevents race conditions)
    if (cachedClientId) {
        return cachedClientId;
    }
    
    // Try to get from sessionStorage
    let clientId = sessionStorage.getItem(CLIENT_ID_KEY);
    
    if (!clientId) {
        clientId = generateClientId();
        sessionStorage.setItem(CLIENT_ID_KEY, clientId);
    }
    
    // Cache it
    cachedClientId = clientId;
    
    return clientId;
}

/**
 * Reset client ID (creates a new isolated session)
 * Useful for "New Session" functionality
 * @returns {string} New client ID
 */
export function resetClientId() {
    const newClientId = generateClientId();
    sessionStorage.setItem(CLIENT_ID_KEY, newClientId);
    return newClientId;
}

/**
 * Check if client ID exists
 * @returns {boolean}
 */
export function hasClientId() {
    return sessionStorage.getItem(CLIENT_ID_KEY) !== null;
}

export default getClientId;


