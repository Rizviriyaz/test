export function isValidCoordinate(lat, lng) {
    if (lat === null || lat === undefined || lng === null || lng === undefined) return false
    const numLat = Number(lat)
    const numLng = Number(lng)
    if (isNaN(numLat) || isNaN(numLng)) return false
    if (numLat === 0 && numLng === 0) return false
    if (numLat < -90 || numLat > 90 || numLng < -180 || numLng > 180) return false
    return true
}
