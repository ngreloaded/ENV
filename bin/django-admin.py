#!/Users/ngreloaded/Desktop/ENV/bin/python3.4
# EASY-INSTALL-DEV-SCRIPT: 'Django==1.7.2','django-admin.py'
__requires__ = 'Django==1.7.2'
import sys
from pkg_resources import require
require('Django==1.7.2')
del require
__file__ = '/Users/ngreloaded/Desktop/ENV/django-stable-1.7.x/django/bin/django-admin.py'
if sys.version_info < (3, 0):
    execfile(__file__)
else:
    exec(compile(open(__file__).read(), __file__, 'exec'))
