(function () {
  const STIMMO = 'https://stimmo.it';

  function findNextData() {
    const el = document.getElementById('__NEXT_DATA__');
    return el ? JSON.parse(el.textContent) : null;
  }

  function findListing(node, depth = 0) {
    if (!node || depth > 14) return null;
    if (
      typeof node === 'object' &&
      !Array.isArray(node) &&
      node.price &&
      node.location &&
      node.typology
    ) {
      return node;
    }
    if (Array.isArray(node)) {
      for (const x of node) {
        const hit = findListing(x, depth + 1);
        if (hit) return hit;
      }
    } else if (typeof node === 'object') {
      for (const k of Object.keys(node)) {
        const hit = findListing(node[k], depth + 1);
        if (hit) return hit;
      }
    }
    return null;
  }

  const data = findNextData();
  const listing = data && findListing(data);
  if (!listing) {
    alert('stimmo: could not find listing data on this page');
    return;
  }

  const payload = {
    price: listing.price?.value,
    surface: listing.surfaceValue ?? listing.surface,
    address: listing.location?.address,
    streetNumber: listing.location?.streetNumber,
    city: listing.location?.city,
    lat: listing.location?.latitude,
    lon: listing.location?.longitude,
    floor: listing.floor?.abbreviation,
    floors: listing.floors,
    elevator: listing.elevator,
    energyClass: listing.energy?.class?.name,
    typologyValue: listing.typologyValue,
    typology: listing.typology?.name,
    category: listing.category?.name,
    conditionId: listing.conditionId,
    condition: listing.condition,
    bathrooms: listing.bathrooms,
    buildingYear: listing.buildingYear,
    primaryFeatures: (listing.primaryFeatures || [])
      .filter((f) => f.value === 1 || f.isVisible)
      .map((f) => f.codeName || f.name)
      .filter(Boolean),
    mainFeatures: (listing.mainFeatures || []).map((f) => f.type).filter(Boolean),
    ga4features: listing.ga4features || [],
    description: (listing.description || '').slice(0, 2000),
  };

  const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(payload))))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

  window.open(STIMMO + '/import?src=immobiliare&v=1&p=' + b64, '_blank');
})();
