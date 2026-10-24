#import <Cocoa/Cocoa.h>
int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSOpenPanel *panel = [NSOpenPanel openPanel];
        panel.title = @"选择音乐下载文件夹";
        panel.message = @"后续下载的音乐将保存在这个文件夹中。";
        panel.prompt = @"选择此文件夹";
        panel.canChooseDirectories = YES;
        panel.canChooseFiles = NO;
        panel.allowsMultipleSelection = NO;
        panel.canCreateDirectories = YES;
        if (argc > 1) panel.directoryURL = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]] isDirectory:YES];
        [app activateIgnoringOtherApps:YES];
        if ([panel runModal] == NSModalResponseOK && panel.URL) {
            printf("%s\n", panel.URL.path.UTF8String);
        }
    }
    return 0;
}
