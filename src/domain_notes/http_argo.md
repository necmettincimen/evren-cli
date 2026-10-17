HTTP VE ARGO CD NOTLARI:
- run_command çıktısı 8000 karakterde kırpılır: büyük JSON yanıtını ham yazdırma. PowerShell
  (use_powershell=true, Invoke-RestMethod) veya python ile yalnızca gereken alanları süzüp yazdır.
- curl gövdesini JSON sanma. İstek `-sS -w "\nHTTP:%{http_code}"` ile gitsin. Kod 200 ve gövde JSON değilse parse etme; kodu ve ilk baytları yaz, aynı aileyi uydurma.
- Argo CD uygulama özeti: `GET /api/v1/applications/{ad}` içindeki `status.resources` (kind, name, sync, health, message).
- Pod ve canlı ağaç: `GET /api/v1/applications/{ad}/resource-tree`. Pod düğümleri `nodes` içindedir (`kind==Pod`).
- Tek nesne: `GET /api/v1/applications/{ad}/resource?namespace=&resourceName=&name=&version=&kind=&group=`.
- Pod logu: `GET /api/v1/applications/{ad}/logs?namespace=&podName=&container=&tailLines=100`. Bu uç metin döner, JSON değildir.
- Şu yollar yoktur; 404 alınca tekrarlama: `/pods`, `/resource/pods`, `/resource/tree`.
- `sync: OutOfSync` ve `health: Missing` kaynak henüz uygulanmamıştır; pod arama. `exceeded its progress deadline` yeni ReplicaSet'in hazır olmamasıdır; resource-tree içindeki Pod ve ardından logs ucuna bak.
