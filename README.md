# Radar de trenes

Sustituye al Space de HuggingFace, que el entorno del radar no puede alcanzar.
GitHub Actions sí alcanza renfe.com, y el radar sí alcanza raw.githubusercontent.com,
así que el circuito se cierra sin tocar la política de red de la organización.

```
GitHub Actions (05:10)  ->  scraper.py  ->  renfe.com
                                    |
                                    v
                         precios-trenes.json (commit al repo)
                                    |
                                    v
        radar de Cowork  <-  raw.githubusercontent.com  (probado, responde 200)
```

## Montarlo (10 minutos, sin tocar código)

1. Crea un repo **público** llamado `radar-trenes`. Público importa: `raw.githubusercontent.com`
   sirve ficheros de repos privados solo con token, y así el radar lee sin credenciales.
2. Sube estos cuatro ficheros respetando las rutas:
   ```
   .github/workflows/radar-trenes.yml
   scraper.py
   rutas.json
   README.md
   ```
3. En el repo: **Settings → Actions → General → Workflow permissions** →
   marca *Read and write permissions*. Sin esto el workflow no puede commitear el JSON.
4. Pestaña **Actions** → *Radar de trenes* → **Run workflow**. Primera ejecución a mano.
5. Cuando termine, comprueba que existe `precios-trenes.json` en la raíz del repo.

La URL que leerá el radar a partir de entonces:

```
https://raw.githubusercontent.com/<tu-usuario>/radar-trenes/main/precios-trenes.json
```

Pásame esa URL y la dejo anotada en la memoria del radar para que el barrido diario
la lea sola.

## La parte que probablemente falle la primera vez

El buscador de Renfe es una SPA y sus selectores cambian cada pocos meses. La función
`rellenar_busqueda()` de `scraper.py` es la única parte frágil: todo lo demás
(programación, contrato JSON, publicación, alertas) es estable.

No pude probar los selectores contra el HTML real porque desde el contenedor del radar
renfe.com está bloqueado igual que el Space. Por eso el workflow guarda **captura de
pantalla y HTML completo** de cada fallo en el artefacto `diagnostico`. Si la primera
ejecución falla:

1. Actions → la ejecución fallida → descarga el artefacto `diagnostico`.
2. Mándamelo y ajusto los selectores en una pasada, ya con el HTML delante.

Esto es lo que espero: infraestructura a la primera, selectores a la segunda.

## Cómo se comporta cuando algo va mal

- **Nunca deja de escribir el JSON.** Un radar sin datos tiene que saber que no los tiene.
- Si fallan todas las rutas, `alerta` lo dice y el radar empieza el aviso con
  "PROBLEMA DEL RADAR:", igual que hacía con el Space.
- Si fallan algunas, `alerta` dice cuántas y el resto de precios siguen sirviendo.
- Si una ventana está a más de 4 meses vista, marca *"fuera de venta todavía"* en vez
  de cantar avería: Renfe solo vende con unos 4 meses de antelación.

## Ajustes que harás tú

- **Rutas y precios de referencia**: `rutas.json`. Los 21 destinos y sus precios
  habituales ya están puestos, sacados de la memoria del radar.
- **Ventanas**: también en `rutas.json`. Cuando pase una, sustitúyela por la siguiente.
- **Hora del barrido**: el `cron` del workflow. Está en UTC.

## Coste

Cero. Repos públicos tienen Actions gratis e ilimitado. El barrido tarda unos
15-25 minutos según cuántas ventanas estén ya a la venta.
