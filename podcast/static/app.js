// Registrace service workeru — bez něj prohlížeč appku nenabídne k instalaci.
// Nic víc tu není: administrace je server-rendered a JavaScript k ničemu nepotřebuje.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js").catch(function (e) {
      console.warn("service worker se nezaregistroval:", e);
    });
  });
}
