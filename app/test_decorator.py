import functools

def csrf_exempt(f):
    f.csrf_exempt = True
    return f

def limiter_limit(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args, **kwargs)
    return wrapper

@csrf_exempt
@limiter_limit
def my_view():
    pass

print("my_view.csrf_exempt:", getattr(my_view, "csrf_exempt", False))

@limiter_limit
@csrf_exempt
def my_view2():
    pass

print("my_view2.csrf_exempt:", getattr(my_view2, "csrf_exempt", False))
