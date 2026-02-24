# Removing unit-tagger

Follow these steps to completely remove unit-tagger once tr-engine has native unit tagging support.

## 1. Stop and disable the service

```bash
sudo systemctl stop unit-tagger
sudo systemctl disable unit-tagger
```

## 2. Remove the service file and reload systemd

```bash
sudo rm /etc/systemd/system/unit-tagger.service
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

## 3. Remove the project directory

```bash
rm -rf /home/brent/unit-tagger
```

## 4. Verify nothing is left running

```bash
ps aux | grep server.py
systemctl status unit-tagger
```

Both should come back empty / "Unit not found".

## 5. Check for orphaned logs

systemd journal entries will naturally rotate on their own, but if you want to purge them immediately:

```bash
sudo journalctl --vacuum-time=1s --unit=unit-tagger
```

---

## Before you remove it — migration notes

unit-tagger writes aliases to two CSV files that trunk-recorder reads via `unitTagsFile`:

| CSV file | System |
|---|---|
| `~/docker/trunk-recorder/configs/pscunits.csv` | pscsite4 |
| `~/docker/trunk-recorder/configs/ipscunits.csv` | ipscpend / ipscand |

**These CSVs are not owned by unit-tagger** — they live in the trunk-recorder config directory and will survive its removal. Before removing, confirm that tr-engine's native tagging has imported or superseded the aliases in those files so no tag data is lost.

Once tr-engine manages tags natively you can also remove the following from each system in `trunk-recorder/configs/config.json`:

```json
"unitTagsFile": "/app/yourfile.csv",
"unitTagsMode": "user"
```

After editing `config.json`, restart the trunk-recorder container via Portainer for the change to take effect.
