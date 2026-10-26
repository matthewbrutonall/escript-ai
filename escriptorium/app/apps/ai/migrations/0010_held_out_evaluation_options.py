from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0009_externalhtrjob_import_link'),
        ('core', '0087_ocrmodel_evaluation_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='ailayergate',
            name='held_out_evaluation',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Model and normalization options used for the held-out CER.',
            ),
        ),
    ]
