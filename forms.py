from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

DATA_KINDS = ["authorized", "synthetic", "public"]


class ImportForm(FlaskForm):
    data_kind = SelectField(choices=DATA_KINDS, default="authorized")


class BuyerImportForm(ImportForm):
    file = FileField(validators=[
        FileRequired(message="请选择名单文件。"),
        FileAllowed(["csv", "xlsx"], message="请上传 CSV 或 XLSX 文件。"),
    ])


class CommentImportForm(ImportForm):
    file = FileField(validators=[
        FileRequired(message="请选择评论文件。"),
        FileAllowed(["csv"], message="请上传评论 CSV 文件。"),
    ])


class CommentForm(FlaskForm):
    body = TextAreaField(validators=[
        DataRequired(message="请填写评论正文。"),
        Length(max=12000, message="评论正文最多 12,000 字。"),
    ])
    data_kind = SelectField(choices=DATA_KINDS, default="authorized")
    source_ref = StringField(validators=[Optional(), Length(max=2048)])
    post_id = StringField(validators=[Optional(), Length(max=200)])
    comment_id = StringField(validators=[Optional(), Length(max=200)])
    thread_id = StringField(validators=[Optional(), Length(max=200)])
    company = StringField(validators=[Optional(), Length(max=200)])
