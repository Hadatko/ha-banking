# HA Banking

**Security is your responsibility.** The author does not accept liability for the security of this integration, for how you configure it, or for what happens to your mailbox, certificates, or bank data. Use it only if you are comfortable with that. Read the code and check it yourself, including with your own AI, before you connect a real account.

Read-only Home Assistant integration for bank accounts. With a bank API it tracks booked and available balances, the latest incoming payment, the latest outgoing payment, and the status of the newest transaction. With email notices it tracks only the latest incoming and outgoing payment. Air Bank is the first bank with a direct API and an email parser. mBank is read from its daily E-mail Push summary. Enable Banking can connect other banks. Support for another bank can be added when someone asks for it or opens a pull request.

It does not create or authorize payments.

If anybody is interested in supporting my "sleepless" nights (working mostly between 23:00-3:00) on different projects press:

[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-%F0%9F%8D%BA-yellow?style=for-the-badge&logo=buy-me-a-coffee)](https://www.buymeacoffee.com/hadatko)

Thank you ;)

Setup offers three sign-in methods:

- **Email notices.** Home Assistant reads Air Bank balance-change emails from your own mailbox. This does not require an account-information licence. It does not read the account balance.
- **Enable Banking.** Each Home Assistant user registers their own Enable Banking application and connects only their own accounts. Enable Banking does not provide this service to a private person residing in the Czech Republic. Untested against the live service.
- **Air Bank certificate.** Direct Open API access for an application that already has an account-information licence and a purchased PSD2 client certificate. Untested against the live API.

Enable Banking and the Air Bank certificate have automated tests with stand-in HTTP replies. Neither has been confirmed with a real account.

Enable Banking is untested because the service does not accept a private person who lives in the Czech Republic. The country list, the bank choice, and the sign-in redirect were never completed on the live site.

The Air Bank certificate is untested because the Open API requires an account-information licence and a PSD2 client certificate bought from a trust service provider. That certificate was not purchased, so the live sign-in and the mutual TLS connection to Air Bank were never made.

## Requirements

- Home Assistant 2025.5 or newer
- The Home Assistant redirect URI registered on the chosen application, when using Enable Banking or the Air Bank certificate. It is the internet address from Settings → System → Network, with `/auth/external/callback` added at the end.
- For email notices, IMAP access to the mailbox that receives those notices.

### Email notices

1. In Air Bank internet banking on a computer, open Účty a karty, choose the account, then Možnosti, then Info o dění na účtu.
2. Send balance changes by email. Set the threshold to the smallest amount the form allows, and include both increases and decreases if you want outgoing payments as well. Air Bank may delay a notice for a change under 500 CZK, especially a card payment.
3. In Home Assistant, choose Email notices and enter the IMAP server, username, password, and a folder for each bank. For Gmail the server is `imap.gmail.com` and the password is an app password. The Air Bank folder defaults to `airbank`. Another bank asks for its own folder. Account names are optional. Put each account on its own line. The number and the name are separated by an equals sign:

    ```text
    1234567890=Main account
    9999999999=Savings account
    ```
 A mapped number is shown under that name. An unmapped number stays `Air Bank` plus the number.
4. **Mark notices as read** is on. Home Assistant marks a message read only after the payment was imported. A message it could not parse stays unread. Turn the option off to leave every message unread. Gmail does not delete a message because it was marked read.

A Gmail filter can apply a label when the message arrives. For Air Bank, use From `info@airbank.cz` and subject `zůstatku`, then choose Skip Inbox and apply the label `airbank`. For Česká spořitelna, use From `ceskasporitelna@csas.cz` and the label `sporitelna`. In Gmail settings, show that label in IMAP. The folder in Home Assistant must be the same name. Another bank can use a different label and a different folder.

Home Assistant reads the mailbox as soon as the integration is added, and again every 15 minutes. Reloading the integration starts another read immediately. There is no separate button that checks the mailbox sooner.

Each read covers the registered bank senders in that folder back to 1 January of the current year, and at least 90 days. It reads that whole window again, so a notice is not added twice and a late notice still joins the month or year of its date. A notice that arrives just after a read waits for the next one, at most 15 minutes.

Air Bank is the first parser. Another bank is another parser beside it, with its own sender and its own account identity. A password-protected account statement is ignored. The newest Air Bank notice sets the available balance from the sentence `Dostupný zůstatek k … je …`. The booked balance is not in the mail, so that sensor stays empty. The account name in front of the account number becomes the device name, unless the account-name list gives that number its own name. Notices are summed for the current year and the current month, each as incoming, outgoing, and net. A notice without a date is left out of those sums.

Česká spořitelna reads payment notices from `ceskasporitelna@csas.cz` in the folder `sporitelna`. A received payment says `na účet … právě dorazila platba ve výši …` and `Na účtu je nově k dispozici …`. A sent payment says `z účtu … odešla platba ve výši …` and `Na účtu zůstává k dispozici …`. The booked balance is not in that mail. The other account number is kept as the counterparty. A mailbox that has no `sporitelna` folder still reads Air Bank.

#### mBank

mBank does not send a mail for each payment. It sends one **E-mail Push** summary each morning, about 06:30, for the previous day. The sender is `kontakt@mbank.cz` and the subject is `mBank – Email Push`. The text of the mail is only a cover note. The table of operations is in the attached HTML file, and Home Assistant reads that attachment.

1. In mBank internet banking, turn on E-mail Push for the account.
2. In Gmail, filter From `kontakt@mbank.cz` with subject `Email Push` and apply a label, for example `mbank`. Show that label in IMAP. Enter the same name as the mBank folder in Home Assistant.

Each row of the summary becomes one payment, dated by the day of the summary and the time in the row. Card payments, incoming and outgoing transfers, instant payments, and direct debits are read. A login confirmation or a standing order that was not carried out is not a payment and is skipped. The available balance comes from the newest row (`Dostup.zust` or `dispo`), which includes any overdraft. The booked balance stays empty. Payments arrive in Home Assistant the morning after they happen, so the announcement of a new payment is up to one day late.

A bank whose folder does not exist in the mailbox is skipped, so an Air Bank user does not need an mBank folder and the other way round. Setup accepts the mailbox when at least one bank folder exists.

### Enable Banking

Untested on the live service. Enable Banking does not provide this service to a private person residing in the Czech Republic, so the sign-in was never finished.

1. Create an account at [Enable Banking](https://enablebanking.com/sign-in/).
2. Register an application with the form below and download its private key. Sandbox and production are chosen there and cannot be switched later.
3. For a production application, whitelist your own accounts in the control panel.
4. In Home Assistant, choose Enable Banking and enter the application ID, the private key path, whether the account is personal or business, and the two-letter country of the bank.
5. Choose the bank from the list Enable Banking returns for that country. One application can be added again for another bank.

The private key must be readable by Home Assistant, for example `/config/ha_banking/enablebanking.pem`. Keep that file secret. Do not commit it or share it.

![Enable Banking form for a new application, with Production selected](docs/enable-banking-new-application.jpg)

Fill the form as follows:

1. **Environment.** Choose **Production** to read a real Air Bank account. Sandbox uses test data and does not show your balances. The choice cannot be changed after registration.
2. **Private key.** Leave **Generate in the browser and export private key** selected. After Register, the browser downloads a PEM file. That file is the private key Home Assistant will use. The other option is only for a key you created yourself.
3. **Application name.** Any name you will recognize, for example `Air Bank Home Assistant`. Enable Banking shows this name when you approve access to the account.
4. **Allowed redirect URLs.** One URL per line. Use the internet address of your Home Assistant and add `/auth/external/callback`. Copy that address from Settings → System → Network. For an address `https://home.example.com` the line is:

    ```text
    https://home.example.com/auth/external/callback
    ```
5. **Description.** What the application does, for example `Read-only tracking of Air Bank balances and transactions in Home Assistant.`
6. **Email for data protection matters.** Your own email address.
7. **Privacy URL.** A public page that says what happens to the account data. For this integration you can use [the privacy section of this README](https://github.com/Hadatko/ha-banking#privacy).
8. **Terms URL.** A public page with the terms of the application. For this integration you can use [the terms section of this README](https://github.com/Hadatko/ha-banking#terms).

Press **Register** and store the downloaded private key on the Home Assistant host. The application ID is the UUID shown for the new application, and it is also the downloaded file name.

### Air Bank certificate

Untested on the live API. Using it requires an account-information licence and a PSD2 client certificate bought from a trust service provider. That certificate was not purchased, so Home Assistant never signed in to Air Bank.

- An Air Bank Open API application with the AISP scope
- A PSD2 client certificate and private key stored on the Home Assistant host, for example `/config/ha_banking/client.pem` and `/config/ha_banking/client.key`

## Install

Add this repository to HACS as a custom integration repository, install HA Banking, and restart Home Assistant. Then add the integration from Settings → Devices & services. The entry lists one hub for each sign-in and one device for each account.

![HA Banking in Settings. One hub contains the accounts added for that sign-in.](docs/settings.png)

## Entities

Each account becomes one device with these sensors. Every sensor shows that bank's logo from the public Enable Banking image, resized for an icon. Air Bank uses `https://enablebanking.com/brands/CZ/Air%20Bank/-/resize/128x/`. Another bank uses the same address with its country and name.

- Booked balance, the overall state of the account
- Available balance
- Last incoming payment, with counterparty, dates, message, reference, and status
- Last outgoing payment, with the same details
- Latest transaction status
- Incoming, outgoing, and net for the current year
- Incoming, outgoing, and net for the current month
- Recent payments, the newest 30 movements in the read window

![Sensors for one account. The amounts are hidden.](docs/sensors.png)

## Dashboard

The integration adds a sidebar dashboard named HA Banking. Its background follows the light or dark mode.

![HA Banking dashboard. The amounts on the chart axes and in the payment list are hidden.](docs/dashboard.png)

Each account starts with the latest payment in each direction and the recent payments. Below that is a line of the available balance for the last year, then bar charts for the same balance, the last 12 months including the current month (incoming, outgoing, net), and this year. The bars use the sensor state, because a monetary sensor does not record an average. A new notice adds another point.

## Notifications

The first read after Home Assistant starts remembers the payments already in the mailbox or the bank history. It does not announce them. A later read, at most 15 minutes afterwards, announces only a payment that was not in that first set.

The announcement is a Home Assistant notification, the item under the bell. It stays until you dismiss it. The same payment also fires the `ha_banking_payment` event, so an automation can send it somewhere else. The integration itself does not push to a phone.

The title follows the Home Assistant language. English uses "New bank payment", Slovak "Nová platba", and Czech "Nová platba". The message is the account name, the direction, the amount, and the currency. When the notice names the other side, that name is added to the message and to the payment list. With English selected, the direction reads Incoming or Outgoing.

Open the integration and choose Configure to set two optional limits. Minimum payment amount announces a payment only when it is at least that amount. Leave it empty to announce every new payment. The `ha_banking_payment` event is still fired for every new payment. Low available balance announces once when the balance falls below the amount, and again only after the balance has risen above it. A restart does not repeat an alert that is already true.

![Alerts dialog, with the minimum payment amount and the low available balance.](docs/alerts.png)

A restart clears that memory and the next first read is quiet again. During one run, the same payment is announced once.

Data is refreshed every 15 minutes. The mailbox is included in that schedule: the first read runs when the email entry is added, and the following reads run every 15 minutes. With the direct Air Bank certificate, a refresh token stays valid for 180 days after the last strong login, and transaction history older than 90 days is only available immediately after that login. The year and month sums use transactions from 1 January, or the last 90 days when that reaches into the previous year. With Enable Banking, the bank consent lasts until the date agreed at sign-in. When it ends, Home Assistant asks for sign-in again. The same date window applies. With email notices, the available balance comes from the newest notice and the booked balance stays empty. The year and month sensors add every parsed notice in that period. Marking a notice read is on unless that option is turned off.

## Privacy

This integration runs inside your Home Assistant. The Enable Banking application ID, private key, Air Bank certificate, and mailbox password stay on that host. Account data is stored as Home Assistant sensor states. This project does not run a server that receives your bank data, and it does not send payments.

Enable Banking and the mailbox provider apply their own privacy terms to those connections. Email notices are read from your IMAP server. Only messages from `info@airbank.cz`, `ceskasporitelna@csas.cz`, and `kontakt@mbank.cz` are parsed.

## Terms

The integration is provided under the MIT license in this repository. It reads balances and transactions, or the latest payment from your own Air Bank emails. It does not create, authorize, or cancel payments. You use your own mailbox, your own Enable Banking application, or your own Air Bank certificate.
