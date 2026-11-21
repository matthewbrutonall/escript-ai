from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ai", "0012_externalhtrjob_layout")]

    operations = [
        migrations.AddField(model_name="aijob", name="error_code", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="aijob", name="error_public", field=models.CharField(blank=True, default="", max_length=400)),
    ]
