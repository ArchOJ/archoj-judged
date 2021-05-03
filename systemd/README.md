## Introduction

We assume Arch OJ Judged is deployed with Systemd. `archoj-judged.service`
creates several CGroups (v1) before launching Arch OJ Judged.

## Setup Systemd Service

Run with root privilege:

```shell
cp ./archoj-judged.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable archoj-judged.service
systemctl start archoj-judged.service
```
