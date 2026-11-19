"""One-time/cached installation; translation itself is fully offline."""
import argostranslate.package as package
import argostranslate.translate as translate

wanted = [('zh', 'en'), ('en', 'ru')]
installed = {(p.from_code, p.to_code) for p in package.get_installed_packages()}
missing = [pair for pair in wanted if pair not in installed]
if missing:
    package.update_package_index()
    available = package.get_available_packages()
    for source, target in missing:
        model = next((p for p in available if p.from_code == source and p.to_code == target), None)
        if model is None:
            raise RuntimeError(f'No offline model: {source}->{target}')
        package.install_from_path(model.download())
for text, language in [('智能机器人', 'zh'), ('Smart home robot', 'en')]:
    print(language, '-> ru:', translate.translate(text, language, 'ru'))
