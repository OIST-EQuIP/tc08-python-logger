from copy import deepcopy
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import smtplib
import ssl


class Mailer:
    def __init__(
        self, subj, mailto, mailfrom_alias=None, smtp_config="smtp_config.txt"
    ) -> None:
        self.smtp_config = {}
        try:
            with open(smtp_config) as f:
                lines = f.readlines()
                for line in lines:
                    k, v = line.split(":")
                    self.smtp_config[k] = v.strip()
        except OSError as e:
            print(f"To initialize SMTP client, please check if {smtp_config} exists.")
            raise e

        if self.smtp_config:
            context = ssl.create_default_context()
            try:
                self.server = smtplib.SMTP(
                    self.smtp_config["host"], self.smtp_config["port"]
                )
                self.server.starttls(context=context)
                self.server.login(
                    self.smtp_config["user"], self.smtp_config["password"]
                )
                self.mail = MIMEMultipart()
                self.mail_new(subj, mailto, mailfrom_alias or self.smtp_config["user"])
            except Exception as e:
                print(self.smtp_config)
                raise e

    def mail_send(self) -> None:
        # assume smtp user is sender
        self.server.sendmail(
            self.smtp_config["user"], self.mailto, self.written_mail.as_string()
        )

    def mail_new(self, subj=None, mailto=None, mailfrom_alias=None) -> None:
        if subj:
            self.mail["Subject"] = subj
        if mailto:
            self.mail["To"] = ", ".join(mailto)
            self.mailto = mailto
        if mailfrom_alias:
            self.mail["From"] = mailfrom_alias

        self.written_mail = deepcopy(self.mail)

    def mail_body(self, body, subtype="plain") -> None:
        self.written_mail.attach(MIMEText(body, subtype))

    def mail_attach(self, bytearray, filename) -> None:
        attachment = MIMEApplication(bytearray)
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        self.written_mail.attach(attachment)
